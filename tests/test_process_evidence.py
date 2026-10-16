import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest
from accounts_payable.evidence import publish
from accounts_payable.schemas import sha
from accounts_payable.service import process_invoice, process_invoice_async
from accounts_payable.store import APStore
from test_native import BODY, FIELDS, Script, TEXT, seed


@pytest.fixture(autouse=True)
def credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-not-a-provider-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    for key in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("phase", ["request", "response", "before-facts", "final"])
def test_recording_fault_refuses_later_publication_and_retains_committed_truth(
    tmp_path, phase
):
    store = seed(tmp_path)
    script = Script([BODY])

    def writer(path, data):
        observed = data.get("model_observation") or {}
        requests = observed.get("provider_observations", [])
        reject = (
            phase == "request"
            and requests
            and requests[-1]["status"] == "prepared"
            or phase == "response"
            and requests
            and requests[-1]["body"] is not None
            or phase == "before-facts"
            and data["publication_status"] == "prepared"
            or phase == "final"
            and data["status"] == "completed"
        )
        if reject:
            raise OSError("synthetic evidence write failure")
        publish(path, data)

    with pytest.raises(Exception) as error:
        process_invoice(store, "mail-1", transport=script, publish=writer)
    data = error.value.evidence
    assert data["status"] == "failed" and data["result"] is None and script.closed
    assert len(script.requests) == (0 if phase == "request" else 1) and len(
        store.list_invoices()
    ) == (1 if phase == "final" else 0)
    assert (
        data["facts_recorded_by_run"] is (phase == "final")
        and store.snapshot()["extraction_leases"] == []
    )
    assert store.ledger_entries() == []


def test_error_after_facts_commit_is_inspected_then_replayed_without_another_model(
    tmp_path, monkeypatch
):
    store = seed(tmp_path)
    original = store.record_facts

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic acknowledgement lost after commit")

    monkeypatch.setattr(store, "record_facts", uncertain)
    with pytest.raises(OSError, match="after commit") as error:
        process_invoice(store, "mail-1", transport=Script([BODY]))
    data = error.value.evidence
    assert (
        data["publication_status"] == "unknown_or_failed"
        and data["facts_recorded_by_run"] is True
    )
    assert (
        len(store.list_invoices()) == 1
        and store.snapshot()["extraction_attempts"][0]["status"] == "facts_recorded"
    )
    monkeypatch.delenv("GOOGLE_API_KEY")
    result = process_invoice(store, "mail-1", transport=Script([]))
    assert (
        result["cached"] is True
        and result["reported_usage"]["prepared_attempts"] == 0
        and len(store.list_invoices()) == 1
    )


def test_source_change_during_native_extraction_refuses_stale_facts(tmp_path):
    store = seed(tmp_path)

    def changed():
        replacement = TEXT + "\nChanged after model request."
        with store._db() as db:
            db.execute(
                "UPDATE inbox SET body=?,sha256=? WHERE source_id=?",
                (replacement, sha(replacement.encode()), "mail-1"),
            )
        return BODY

    with pytest.raises(Exception, match="source changed") as error:
        process_invoice(store, "mail-1", transport=Script([changed]))
    assert (
        store.list_invoices() == []
        and error.value.evidence["model_observation"]["canonical_facts"]["total_minor"]
        == 12500
    )
    assert (
        error.value.evidence["source"]["body"] == TEXT
        and store.source_text("mail-1") != TEXT
        and store.snapshot()["extraction_leases"] == []
    )


def test_replaced_extraction_owner_is_not_released_by_the_old_native_run(tmp_path):
    store = seed(tmp_path)
    replacement = []

    def changed():
        old = store.snapshot()["extraction_leases"][0]
        store.release_extraction("mail-1", old["token"], "Synthetic stopped owner")
        replacement.append(
            store.claim_extraction(
                "mail-1", "replacement", "/tmp/replacement", "model_extraction"
            )
        )
        return BODY

    with pytest.raises(Exception, match="ownership changed"):
        process_invoice(store, "mail-1", transport=Script([changed]))
    assert (
        store.list_invoices() == []
        and store.snapshot()["extraction_leases"][0]["token"] == replacement[0]["token"]
    )
    store.release_extraction("mail-1", replacement[0]["token"], "Test owner cleanup")


def test_same_source_exclusion_precedes_a_second_model_and_different_sources_recover(
    tmp_path,
):
    import httpx

    store = seed(tmp_path)
    store.ingest("other-source", TEXT.replace("INV-1048", "INV-1049"))

    async def exercise():
        entered = asyncio.Event()
        closed = []

        class Pending(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.append(True)

        task = asyncio.create_task(
            process_invoice_async(store, "mail-1", transport=Pending())
        )
        await asyncio.wait_for(entered.wait(), 3)
        unused = Script([])
        with pytest.raises(ValueError, match="already processing"):
            await process_invoice_async(store, "mail-1", transport=unused)
        assert unused.requests == [] and unused.closed is False
        alternate = deepcopy(BODY)
        alternate["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(
            {**FIELDS, "invoice_number": "INV-1049"}
        )
        result = await process_invoice_async(
            store, "other-source", transport=Script([alternate])
        )
        assert result["invoice"]["source_id"] == "other-source"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed and store.snapshot()["extraction_leases"] == []
        recovered = await process_invoice_async(
            store, "mail-1", transport=Script([BODY])
        )
        assert recovered["invoice"]["source_id"] == "mail-1"

    asyncio.run(exercise())
    assert len(store.list_invoices()) == 2 and store.ledger_entries() == []
