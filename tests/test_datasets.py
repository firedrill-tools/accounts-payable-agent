"""Fixture integrity, imported human history and native source-only prompts."""

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest
from accounts_payable.datasets import (
    Fixture,
    data_root,
    initialize,
    list_cases,
    load_manifest,
)
from accounts_payable.schemas import sha
from accounts_payable.service import process_invoice
from accounts_payable.store import APStore
from test_native import BODY, Script


@pytest.mark.parametrize("case", list_cases()["cases"], ids=lambda case: case["id"])
def test_each_hash_bound_case_initializes_privately_without_model_history(
    tmp_path, case
):
    database = tmp_path / case["id"] / "ap.db"
    initialized = initialize(case["id"], database)
    state = APStore(database).snapshot()
    assert initialized["source_id"] == case["source_id"]
    assert initialized["fixture_sha256"] == case["sha256"]
    assert (
        len(state["vendors"]) == case["vendors"]
        and len(state["invoices"]) == case["prior_invoices"]
    )
    assert (
        len(state["inbox"]) == case["sources"]
        and len(state["purchase_orders"]) == case["purchase_orders"]
    )
    assert all(
        row["facts_origin"] == "human_import" and row["extraction_run_id"] is None
        for row in state["invoices"]
    )
    assert all(row["origin"] == "human_import" for row in state["actions"])
    assert state["extraction_attempts"] == [] and state["extraction_leases"] == []
    assert (
        database.stat().st_mode & 0o777 == 0o600
        and database.parent.stat().st_mode & 0o777 == 0o700
    )
    before = database.read_bytes()
    with pytest.raises(FileExistsError):
        initialize(case["id"], database)
    assert database.read_bytes() == before
    if case["id"] == "busy":
        assert len(state["ledger"]) == 6 and len(state["actions"]) == 15
        assert sum(row["status"] == "approved" for row in state["invoices"]) == 2
        assert {row["currency"] for row in state["purchase_orders"]} == {
            "USD",
            "CAD",
            "EUR",
            "GBP",
            "JPY",
            "KWD",
        }


@pytest.mark.parametrize(
    "kind", ["hash", "traversal", "symlink", "relationship", "boolean-version"]
)
def test_fixture_tampering_and_escaped_paths_are_rejected(tmp_path, kind):
    root = tmp_path / "data"
    shutil.copytree(data_root(), root)
    manifest = json.loads((root / "manifest.json").read_text())
    target = root / manifest["cases"][0]["file"]
    if kind == "hash":
        target.write_text(target.read_text() + "\n")
    if kind == "traversal":
        manifest["cases"][0]["file"] = "../outside.json"
    if kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
    if kind in {"relationship", "boolean-version"}:
        content = json.loads(target.read_text())
        if kind == "relationship":
            content["purchase_orders"][0]["vendor_id"] = "unregistered"
        else:
            content["dataset"]["version"] = True
        target.write_text(json.dumps(content))
        manifest["cases"][0]["sha256"] = sha(target.read_bytes())
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_manifest(root)


def test_actual_busy_native_request_contains_selected_source_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-not-a-provider-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    database = tmp_path / "busy.db"
    initialize("busy", database)
    store = APStore(database)
    selected = store.source_text("mail-b013")
    # Reuse the extraction shape; the source is the sole authority, never fixture focus/history.
    fixture = json.loads((data_root() / "cases/busy.json").read_text())
    po = fixture["purchase_orders"][12]
    vendor = next(
        row for row in fixture["vendors"] if row["vendor_id"] == po["vendor_id"]
    )
    facts = {
        "vendor_name": vendor["name"],
        "invoice_number": "B-INV-1013",
        "po_number": po["po_number"],
        "currency": po["currency"],
        "total_text": selected.split("Total due: ")[1].strip().split(" ", 1)[1],
        "due_date": "2026-11-13",
        "concerns": [],
    }
    body = deepcopy(BODY)
    body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(facts)
    transport = Script([body])
    result = process_invoice(
        store, "mail-b013", transport=transport, output_root=tmp_path / "runs"
    )
    request = transport.requests[0]["body"]
    parts = request["contents"][0]["parts"]
    assert parts == [{"text": selected}]
    assert "focus" not in json.dumps(request) and "human_import" not in json.dumps(
        request
    )
    assert (
        result["invoice"]["status"] == "pending_approval"
        and result["invoice"]["total_minor"] == po["total_minor"]
    )
    assert len(store.ledger_entries()) == 6 and len(store.list_invoices()) == 13
