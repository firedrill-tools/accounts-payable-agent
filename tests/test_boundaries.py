import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest
from accounts_payable.schemas import (
    ExtractedInvoice,
    InvoiceFacts,
    parse_amount,
    to_facts,
)
from accounts_payable.store import APStore


def setup(tmp_path):
    store = APStore(tmp_path / "private/ap.db")
    store.seed_demo()
    return store


def record(store, source="mail", number="INV-1048", po="PO-204"):
    store.ingest(
        source,
        f"Invoice {number} Northstar Paper Supply {po} USD 125.00 Due 2026-11-13",
    )
    return store.record_facts(
        source,
        InvoiceFacts(
            vendor_name="Northstar Paper Supply",
            invoice_number=number,
            po_number=po,
            currency="USD",
            total_minor=12500,
            due_date="2026-11-13",
        ),
    )


def test_canonical_invoice_identity_cannot_double_post_against_another_po(tmp_path):
    store = setup(tmp_path)
    store.set_po("PO-205", "vendor-northstar", "USD", 12500)
    first = record(store)
    store.approve(
        first["invoice_id"],
        "Human operator",
        expected_review=store.review(first["invoice_id"])["review_fingerprint"],
    )
    store.post(first["invoice_id"])
    second = record(store, "second", "inv\u20111048", "PO-205")
    assert (
        second["status"] == "duplicate"
        and f"duplicate of invoice {first['invoice_id']}" in second["issues"]
    )
    with pytest.raises(ValueError):
        store.approve(
            second["invoice_id"],
            "Human operator",
            expected_review=store.review(second["invoice_id"])["review_fingerprint"],
        )
    assert len(store.ledger_entries()) == 1


def test_ambiguous_canonical_vendor_names_never_select_one_identity(tmp_path):
    store = setup(tmp_path)
    store.add_vendor("vendor-wide", "Ｎorthstar Paper Supply")
    invoice = record(store)
    assert (
        invoice["status"] == "needs_review"
        and invoice["vendor_id"] is None
        and "ambiguous vendor identity" in invoice["issues"]
    )


def test_current_review_fingerprint_is_required_and_po_revision_invalidates_it(
    tmp_path,
):
    store = setup(tmp_path)
    invoice = record(store)
    id = invoice["invoice_id"]
    review = store.review(id)
    assert "USD 125.00" in review["source"]["body"]
    with pytest.raises(ValueError, match="fingerprint"):
        store.approve(id, "Human operator")
    store.set_po("PO-204", "vendor-northstar", "USD", 14500)
    store.set_po("PO-204", "vendor-northstar", "USD", 12500)
    with pytest.raises(ValueError, match="fingerprint changed"):
        store.approve(
            id, "Human operator", expected_review=review["review_fingerprint"]
        )
    current = store.review(id)
    assert current["review_fingerprint"] != review["review_fingerprint"]
    store.approve(id, "Human operator", expected_review=current["review_fingerprint"])
    store.set_vendor_active("vendor-northstar", False)
    store.set_vendor_active("vendor-northstar", True)
    stopped = store.post(id)
    assert stopped["status"] == "needs_review" and stopped["approval_actor"] is None
    assert (
        any("approval snapshot changed" in issue for issue in stopped["issues"])
        and store.ledger_entries() == []
    )


def test_approval_and_post_history_keep_the_original_binding_on_exact_post_retry(
    tmp_path,
):
    store = setup(tmp_path)
    invoice = record(store)
    review = store.review(invoice["invoice_id"])
    store.approve(
        invoice["invoice_id"], "Reviewer", expected_review=review["review_fingerprint"]
    )
    first = store.post(invoice["invoice_id"])
    second = store.post(invoice["invoice_id"])
    assert first["posting_replayed"] is False and second["posting_replayed"] is True
    assert (
        len(store.actions(invoice["invoice_id"])) == 2
        and len(store.ledger_entries()) == 1
    )
    assert json.loads(store.ledger_entries()[0]["binding_json"]) == review["binding"]


def test_source_or_fact_edits_refuse_stale_human_approval(tmp_path):
    store = setup(tmp_path)
    invoice = record(store)
    review = store.review(invoice["invoice_id"])
    with store._db() as db:
        db.execute(
            "UPDATE inbox SET body=body||? WHERE source_id=?", (" changed", "mail")
        )
    with pytest.raises(ValueError, match="source bytes"):
        store.approve(
            invoice["invoice_id"],
            "Reviewer",
            expected_review=review["review_fingerprint"],
        )
    assert store.ledger_entries() == [] and store.actions(invoice["invoice_id"]) == []


@pytest.mark.parametrize(
    "code,text,expected",
    [
        ("USD", "1,250.00", 125000),
        ("JPY", "125.00", 125),
        ("KWD", "12.345", 12345),
        ("USD", "90071992547409.91", 9007199254740991),
    ],
)
def test_exact_minor_conversion_has_no_decimal_rounding(code, text, expected):
    assert parse_amount(text, code) == expected


@pytest.mark.parametrize(
    "code,text",
    [
        ("USD", "1,25.00"),
        ("JPY", "125.01"),
        ("KWD", "12.3456"),
        ("USD", "90071992547409.92"),
        ("USD", "1e4"),
        ("USD", "-125.00"),
    ],
)
def test_unsupported_or_fractional_minor_amounts_are_not_silently_rounded(code, text):
    with pytest.raises(ValueError):
        parse_amount(text, code)


def test_model_amount_conversion_and_source_coverage_do_not_invent_missing_evidence():
    raw = ExtractedInvoice(
        vendor_name="Northstar Paper Supply",
        invoice_number="INV-1048",
        po_number="PO-204",
        currency="USD",
        total_text="125.00",
    )
    valid = to_facts(raw, "Northstar Paper Supply Invoice INV-1048 PO-204 USD 125.00")
    assert valid.total_minor == 12500 and valid.concerns == ()
    bad = to_facts(raw, "Northstar Paper Supply Invoice INV-10480 PO-204 USD 1250.00")
    assert bad.total_minor is None and bad.invoice_number is None and bad.concerns


def test_extraction_claim_is_exclusive_and_old_tokens_cannot_release_a_new_owner(
    tmp_path,
):
    store = setup(tmp_path)
    store.ingest("mail", "Synthetic invoice")
    ready = threading.Barrier(2)

    def claim(index):
        ready.wait()
        try:
            return store.claim_extraction(
                "mail", f"run-{index}", f"/tmp/receipt-{index}", "model_extraction"
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [1, 2]))
    claims = [result for result in results if isinstance(result, dict)]
    assert len(claims) == 1
    token = claims[0]["token"]
    assert store.release_extraction("mail", token, "Synthetic stopped owner") is True
    replacement = store.claim_extraction(
        "mail", "replacement", "/tmp/replacement", "model_extraction"
    )
    assert store.release_extraction("mail", token, "late old cleanup") is False
    assert store.snapshot()["extraction_leases"][0]["token"] == replacement["token"]
    with pytest.raises(ValueError, match="ownership changed"):
        store.record_facts(
            "mail",
            InvoiceFacts(),
            origin="model_extraction",
            run_id="old-run",
            lease_token=token,
        )
    assert store.list_invoices() == []


def test_new_db_and_journals_are_private_and_invalid_dates_fail(tmp_path):
    store = setup(tmp_path)
    assert (
        Path(store.path).stat().st_mode & 0o777 == 0o600
        and Path(store.path).parent.stat().st_mode & 0o777 == 0o700
    )
    with store._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE vendors SET active=active")
        for suffix in ("-wal", "-shm"):
            path = Path(store.path + suffix)
            if path.exists():
                assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        InvoiceFacts(due_date="2026-2-03")


def test_model_invented_iso_due_date_is_cleared_and_requires_review():
    raw = ExtractedInvoice(
        vendor_name="Northstar Paper Supply",
        invoice_number="INV-1048",
        po_number="PO-204",
        currency="USD",
        total_text="125.00",
        due_date="2026-11-13",
    )
    facts = to_facts(
        raw,
        "Northstar Paper Supply Invoice INV-1048 PO-204 USD 125.00 No due date printed.",
    )
    assert facts.due_date is None and any(
        "due_date" in concern for concern in facts.concerns
    )
