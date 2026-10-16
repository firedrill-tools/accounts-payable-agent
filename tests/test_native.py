"""Actual ADK/Gemini/SDK HTTP through a public transport, no canned model class."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from accounts_payable import agent as agent_module
from accounts_payable.agent import ADKInvoiceExtractor
from accounts_payable.service import process_invoice, process_invoice_async
from accounts_payable.store import APStore

ROOT = Path(__file__).parents[1]
TEXT = (ROOT / "fixtures/northstar-1048.txt").read_text()
FIELDS = {
    "vendor_name": "Northstar Paper Supply",
    "invoice_number": "INV-1048",
    "po_number": "PO-204",
    "currency": "USD",
    "total_text": "125.00",
    "due_date": "2026-11-13",
    "concerns": [],
}
USAGE = {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}
BODY = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": json.dumps(FIELDS)}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": USAGE,
    "modelVersion": "gemini-2.5-flash-synthetic",
    "responseId": "synthetic-response",
    "unknown_field": {"preserved": True},
}


@pytest.fixture(autouse=True)
def native_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-not-a-provider-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    for key in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(key, raising=False)


def seed(tmp_path):
    store = APStore(tmp_path / "ap.db")
    store.seed_demo()
    store.ingest("mail-1", TEXT)
    return store


class Script(httpx.AsyncBaseTransport):
    def __init__(self, items):
        self.items = iter(items)
        self.requests = []
        self.closed = False

    async def handle_async_request(self, request):
        self.requests.append(
            {"path": request.url.path, "body": json.loads(request.content)}
        )
        value = next(self.items)
        if callable(value):
            value = value()
        if isinstance(value, int):
            return httpx.Response(
                value,
                json={
                    "error": {
                        "code": value,
                        "message": "Synthetic provider unavailable",
                    },
                    "usageMetadata": USAGE,
                },
                headers={"x-request-id": "failed-request"},
            )
        if isinstance(value, bytes):
            return httpx.Response(
                200,
                content=value,
                headers={
                    "content-type": "application/json",
                    "x-request-id": "malformed-request",
                },
            )
        return httpx.Response(
            200, json=value, headers={"x-request-id": "synthetic-request"}
        )

    async def aclose(self):
        self.closed = True


def test_actual_native_adk_extraction_records_source_and_owns_lifecycle(tmp_path):
    store = seed(tmp_path)
    store.add_vendor("private-other", "Unrelated directory secret")
    script = Script([BODY])
    result = process_invoice(store, "mail-1", transport=script)
    invoice = result["invoice"]
    data = json.loads(Path(result["receipt_path"]).read_text())
    assert (
        invoice["status"] == "pending_approval"
        and invoice["total_minor"] == 12500
        and invoice["total_text"] == "125.00"
        and invoice["facts_origin"] == "model_extraction"
    )
    assert (
        invoice["approval_actor"] is None
        and store.ledger_entries() == []
        and store.actions(invoice["invoice_id"]) == []
    )
    assert (
        len(script.requests) == 1
        and script.closed
        and script.requests[0]["path"].endswith("gemini-2.5-flash:generateContent")
    )
    request = script.requests[0]["body"]
    assert (
        request["generationConfig"]["maxOutputTokens"] == 4096
        and request["generationConfig"]["candidateCount"] == 1
    )
    prompt = json.dumps(request["contents"])
    assert "INV-1048" in prompt and "Unrelated directory secret" not in prompt
    observed = data["model_observation"]
    assert (
        all(observed["lifecycle"].values())
        and observed["provider_observations"][0]["body"] == BODY
    )
    assert (
        observed["provider_observations"][0]["request_id"] == "synthetic-request"
        and observed["provider_observations"][0]["response_model"]
        == BODY["modelVersion"]
    )
    assert (
        result["reported_usage"]["fields"] == USAGE
        and result["reported_usage"]["complete"] is True
        and result["cost_usd"] is None
    )
    assert (
        data["facts_recorded_by_run"] is True
        and data["publication_status"] == "committed"
        and data["actual_invoice"]["invoice_id"] == invoice["invoice_id"]
    )
    assert (
        store.snapshot()["extraction_leases"] == []
        and store.snapshot()["extraction_attempts"][0]["status"] == "facts_recorded"
    )
    assert (
        "synthetic-not-a-provider-key" not in json.dumps(data)
        and Path(result["receipt_path"]).stat().st_mode & 0o777 == 0o600
    )


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "OTHER", None])
def test_native_unfinished_structured_facts_never_commit(tmp_path, finish):
    store = seed(tmp_path)
    body = deepcopy(BODY)
    body["candidates"][0]["finishReason"] = finish
    script = Script([body])
    with pytest.raises(Exception) as error:
        process_invoice(store, "mail-1", transport=script)
    data = error.value.evidence
    assert (
        data["status"] == "failed"
        and data["result"] is None
        and store.list_invoices() == []
    )
    assert (
        data["model_observation"]["provider_observations"][0]["body"] == body
        and data["reported_usage"]["fields"] == USAGE
    )
    assert (
        script.closed
        and len(script.requests) == 1
        and store.snapshot()["extraction_leases"] == []
    )
    assert data["model_observation"]["lifecycle"]["session_deleted"] is True


@pytest.mark.parametrize(
    "kind",
    ["multiple", "empty", "blocked", "function", "invalid-json", "oversized", "schema"],
)
def test_unsupported_or_malformed_native_content_does_not_commit(tmp_path, kind):
    store = seed(tmp_path)
    body = deepcopy(BODY)
    if kind == "multiple":
        body["candidates"] *= 2
    if kind == "empty":
        body["candidates"][0]["content"]["parts"] = []
    if kind == "blocked":
        body["promptFeedback"] = {"blockReason": "SAFETY"}
    if kind == "function":
        body["candidates"][0]["content"]["parts"].append(
            {"functionCall": {"name": "approve_invoice", "args": {}}}
        )
    if kind == "invalid-json":
        body = b"{synthetic malformed JSON"
    if kind == "oversized":
        body = (json.dumps(body) + " " * 1_048_576).encode()
    if kind == "schema":
        body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(
            {**FIELDS, "approval_actor": "Invented operator"}
        )
    script = Script([body])
    with pytest.raises(Exception) as error:
        process_invoice(store, "mail-1", transport=script)
    assert (
        error.value.evidence["result"] is None
        and store.list_invoices() == []
        and store.ledger_entries() == []
        and script.closed
        and len(script.requests) == 1
    )
    assert store.snapshot()["extraction_leases"] == []


@pytest.mark.parametrize(
    "usage", ["missing", "zero", "partial", "invalid", "independent-total"]
)
def test_original_native_usage_is_distinct_from_sdk_defaults_and_sums(tmp_path, usage):
    store = seed(tmp_path)
    body = deepcopy(BODY)
    if usage == "missing":
        body.pop("usageMetadata")
    if usage == "zero":
        body["usageMetadata"] = {key: 0 for key in USAGE}
    if usage == "partial":
        body["usageMetadata"] = {"promptTokenCount": 7}
    if usage == "invalid":
        body["usageMetadata"] = {
            "promptTokenCount": True,
            "candidatesTokenCount": -1,
            "totalTokenCount": "15",
        }
    if usage == "independent-total":
        body["usageMetadata"]["totalTokenCount"] = 99
    script = Script([body])
    try:
        result = process_invoice(store, "mail-1", transport=script)
        data = json.loads(Path(result["receipt_path"]).read_text())
    except Exception as error:
        data = error.evidence
        assert data["result"] is None
    fields = data["reported_usage"]["fields"]
    assert data["model_observation"]["provider_observations"][0]["body"] == body
    if usage in {"missing", "invalid"}:
        assert fields == {key: None for key in USAGE}
    if usage == "zero":
        assert fields == {key: 0 for key in USAGE}
    if usage == "partial":
        assert fields == {
            "promptTokenCount": 7,
            "candidatesTokenCount": None,
            "totalTokenCount": None,
        }
    if usage == "independent-total":
        assert fields["totalTokenCount"] == 99
    assert data["reported_usage"]["cost_usd"] is None and script.closed


def test_native_503_has_one_attempt_and_retains_failed_measurements(tmp_path):
    store = seed(tmp_path)
    script = Script([503])
    with pytest.raises(Exception) as error:
        process_invoice(store, "mail-1", transport=script)
    data = error.value.evidence
    assert (
        data["reported_usage"]["fields"] == USAGE
        and data["model_observation"]["provider_observations"][0]["http_status"] == 503
    )
    assert (
        len(script.requests) == 1
        and script.closed
        and store.list_invoices() == []
        and store.snapshot()["extraction_leases"] == []
    )


def test_existing_facts_replay_needs_no_key_and_is_not_a_new_model_trial(
    tmp_path, monkeypatch
):
    store = seed(tmp_path)
    first = process_invoice(store, "mail-1", transport=Script([BODY]))
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.delenv("GEMINI_MODEL")
    unused = Script([])
    second = process_invoice(store, "mail-1", transport=unused)
    assert (
        second["cached"] is True
        and second["execution_kind"] == "model_free_existing_invoice"
        and second["invoice"]["invoice_id"] == first["invoice"]["invoice_id"]
    )
    assert (
        second["reported_usage"]["prepared_attempts"] == 0
        and second["reported_usage"]["complete"] is False
        and all(value is None for value in second["reported_usage"]["fields"].values())
    )
    assert (
        unused.requests == []
        and unused.closed is False
        and len(store.snapshot()["extraction_attempts"]) == 1
    )


@pytest.mark.parametrize(
    "variable", ["GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"]
)
def test_ambient_backend_is_rejected_before_client_or_http(
    tmp_path, monkeypatch, variable
):
    store = seed(tmp_path)
    monkeypatch.setenv(variable, "true")
    script = Script([])
    with pytest.raises(Exception, match="Developer API") as error:
        process_invoice(store, "mail-1", transport=script)
    assert (
        script.requests == []
        and error.value.evidence["reported_usage"]["prepared_attempts"] == 0
        and store.snapshot()["extraction_leases"] == []
    )


def test_runner_startup_failure_closes_both_owned_clients(tmp_path, monkeypatch):
    store = seed(tmp_path)
    script = Script([])

    def broken(*args):
        raise RuntimeError("synthetic runner startup failure")

    monkeypatch.setattr(agent_module, "build_runner", broken)
    with pytest.raises(Exception, match="runner startup") as error:
        process_invoice(store, "mail-1", transport=script)
    lifecycle = error.value.evidence["model_observation"]["lifecycle"]
    assert (
        lifecycle["sdk_closed"]
        and lifecycle["http_closed"]
        and script.closed
        and script.requests == []
    )


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_native_cancellation_deadline_cleanup_and_later_retry(tmp_path, kind):
    store = seed(tmp_path)

    async def exercise():
        entered = asyncio.Event()
        closed = []
        cancelled = []

        class Pending(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)

            async def aclose(self):
                closed.append(True)

        task = asyncio.create_task(
            process_invoice_async(
                store,
                "mail-1",
                transport=Pending(),
                timeout_seconds=1 if kind == "deadline" else 120,
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        if kind == "cancel":
            task.cancel()
        with pytest.raises(
            TimeoutError if kind == "deadline" else asyncio.CancelledError
        ) as error:
            await task
        assert (
            cancelled
            and closed
            and error.value.evidence["result"] is None
            and store.list_invoices() == []
            and store.snapshot()["extraction_leases"] == []
        )
        lifecycle = error.value.evidence["model_observation"]["lifecycle"]
        assert (
            lifecycle["session_deleted"]
            and lifecycle["runner_closed"]
            and lifecycle["http_closed"]
            and lifecycle["sdk_closed"]
        )
        recovered = await process_invoice_async(
            store, "mail-1", transport=Script([BODY])
        )
        assert recovered["invoice"]["status"] == "pending_approval"

    asyncio.run(exercise())
