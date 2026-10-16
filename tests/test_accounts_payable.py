import asyncio

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from accounts_payable.agent import ADKInvoiceExtractor, InvoiceFacts
from accounts_payable.store import APStore


class StubInvoiceModel(BaseLlm):
    model: str = "synthetic-invoice-stub"

    async def generate_content_async(self, llm_request, stream=False):
        facts = InvoiceFacts(vendor_name="Northstar Paper Supply", invoice_number="INV-1048",
                             po_number="PO-204", currency="USD", total_minor=12500,
                             due_date="2026-11-13")
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part.from_text(text=facts.model_dump_json())
        ]))


def facts(**changes):
    base = dict(vendor_name="Northstar Paper Supply", invoice_number="INV-1048",
                po_number="PO-204", currency="USD", total_minor=12500,
                due_date="2026-11-13")
    return InvoiceFacts(**(base | changes))


@pytest.fixture
def store(tmp_path):
    result = APStore(tmp_path / "ap.db")
    result.seed_demo()
    return result


def record(store, source="mail-1", **changes):
    store.ingest(source, f"synthetic invoice {source}")
    return store.record_facts(source, facts(**changes))


def test_real_adk_runner_with_local_model_stub():
    result = asyncio.run(ADKInvoiceExtractor(model=StubInvoiceModel())("synthetic invoice"))
    assert result.invoice_number == "INV-1048"
    assert result.total_minor == 12500


def test_match_requires_approval_then_posts_once(store):
    invoice = record(store)
    assert invoice["status"] == "pending_approval"
    with pytest.raises(ValueError, match="expected approved"):
        store.post(invoice["invoice_id"])
    approved = store.approve(invoice["invoice_id"], "Morgan")
    assert approved["status"] == "approved"
    assert store.post(invoice["invoice_id"])["status"] == "posted"
    assert store.ledger_entries()[0]["total_minor"] == 12500
    assert [action["action"] for action in store.actions(invoice["invoice_id"])] == [
        "approved", "posted"]
    with pytest.raises(ValueError, match="expected approved"):
        store.post(invoice["invoice_id"])
    assert len(store.ledger_entries()) == 1


def test_source_replay_is_idempotent_and_conflicting_content_fails(store):
    assert store.ingest("mail-1", "Invoice A")["replayed"] is False
    assert store.ingest("mail-1", "Invoice A")["replayed"] is True
    with pytest.raises(ValueError, match="different invoice content"):
        store.ingest("mail-1", "Invoice B")
    first = store.record_facts("mail-1", facts())
    assert store.record_facts("mail-1", facts(total_minor=100))["invoice_id"] == first["invoice_id"]
    assert len(store.list_invoices()) == 1


def test_duplicate_invoice_from_second_source_cannot_be_approved(store):
    first = record(store, "mail-1")
    second = record(store, "mail-2")
    assert second["status"] == "duplicate"
    assert f"duplicate of invoice {first['invoice_id']}" in second["issues"]
    with pytest.raises(ValueError, match="expected pending_approval"):
        store.approve(second["invoice_id"], "Morgan")
    assert store.ledger_entries() == []


def test_unknown_vendor_and_po_recover_after_directory_update(store):
    invoice = record(store, vendor_name="Cedarline Equipment", invoice_number="INV-9901",
                     po_number="PO-990", total_minor=41000)
    assert invoice["status"] == "needs_review"
    assert "unknown vendor" in invoice["issues"]
    with pytest.raises(ValueError, match="expected pending_approval"):
        store.approve(invoice["invoice_id"], "Morgan")
    store.add_vendor("vendor-cedarline", "Cedarline Equipment")
    store.set_po("PO-990", "vendor-cedarline", "USD", 41000)
    assert store.reconcile(invoice["invoice_id"])["status"] == "pending_approval"
    store.approve(invoice["invoice_id"], "Morgan")
    assert store.post(invoice["invoice_id"])["status"] == "posted"


def test_amount_discrepancy_and_model_concern_require_review(store):
    bad_amount = record(store, "mail-1", total_minor=14500)
    assert bad_amount["status"] == "needs_review"
    assert "purchase order amount mismatch" in bad_amount["issues"]
    ambiguous = record(store, "mail-2", invoice_number="INV-1050",
                       concerns=["total is partially unreadable"])
    assert ambiguous["status"] == "needs_review"
    assert "total is partially unreadable" in ambiguous["issues"]
    assert store.ledger_entries() == []


def test_changed_po_after_approval_stops_post_and_clears_approval(store):
    invoice = record(store)
    store.approve(invoice["invoice_id"], "Morgan")
    store.set_po("PO-204", "vendor-northstar", "USD", 14500)
    stopped = store.post(invoice["invoice_id"])
    assert stopped["status"] == "needs_review"
    assert stopped["approval_actor"] is None
    assert "purchase order amount mismatch" in stopped["issues"]
    assert store.ledger_entries() == []
    store.set_po("PO-204", "vendor-northstar", "USD", 12500)
    assert store.reconcile(invoice["invoice_id"])["status"] == "pending_approval"
    store.approve(invoice["invoice_id"], "Alex")
    assert store.post(invoice["invoice_id"])["status"] == "posted"


def test_rejection_blocks_post_and_missing_fields_never_approve(store):
    ready = record(store, "mail-1")
    assert store.reject(ready["invoice_id"], "Morgan")["status"] == "rejected"
    with pytest.raises(ValueError, match="expected approved"):
        store.post(ready["invoice_id"])
    incomplete = record(store, "mail-2", invoice_number=None, total_minor=None)
    assert incomplete["status"] == "needs_review"
    assert "missing invoice number" in incomplete["issues"]
    assert "missing or nonpositive total" in incomplete["issues"]
    assert store.ledger_entries() == []


@pytest.mark.parametrize('value',[True,12500.0])
def test_minor_units_are_strict_integers_not_coerced_values(value):
    with pytest.raises(ValueError):InvoiceFacts(total_minor=value)


def test_two_approved_invoices_cannot_bill_the_same_purchase_order_twice(store):
    first=record(store,'mail-a',invoice_number='INV-A');second=record(store,'mail-b',invoice_number='INV-B')
    store.approve(first['invoice_id'],'Morgan');store.approve(second['invoice_id'],'Alex')
    assert store.post(first['invoice_id'])['status']=='posted'
    blocked=store.post(second['invoice_id'])
    assert blocked['status']=='needs_review' and blocked['approval_actor'] is None
    assert 'purchase order remaining balance exceeded' in blocked['issues']
    assert sum(entry['total_minor'] for entry in store.ledger_entries())==12500
    assert store.ledger_entries()[0]['po_number']=='PO-204'
    third=record(store,'mail-c',invoice_number='INV-C')
    assert third['status']=='needs_review' and 'purchase order remaining balance exceeded' in third['issues']


def test_concurrent_posting_rechecks_remaining_po_balance_inside_the_transaction(store):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    invoices=[record(store,f'mail-{index}',invoice_number=f'INV-{index}') for index in (1,2)]
    for invoice in invoices:store.approve(invoice['invoice_id'],'Human operator')
    ready=threading.Barrier(2)
    def post(invoice):ready.wait();return store.post(invoice['invoice_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(post,invoices))
    assert sorted(invoice['status'] for invoice in results)==['needs_review','posted']
    assert len(store.ledger_entries())==1 and sum(entry['total_minor'] for entry in store.ledger_entries())==12500


def test_po_identity_change_after_prior_posting_does_not_mix_vendor_or_currency_budgets(store):
    first=record(store,'mail-first');store.approve(first['invoice_id'],'Morgan');store.post(first['invoice_id'])
    store.set_po('PO-204','vendor-orbit','EUR',12500)
    second=record(store,'mail-second',vendor_name='Orbit Facilities',invoice_number='ORBIT-NEW',currency='EUR')
    assert second['status']=='needs_review' and 'purchase order identity changed after prior posting' in second['issues']
    with pytest.raises(ValueError,match='expected pending_approval'):store.approve(second['invoice_id'],'Alex')
    assert len(store.ledger_entries())==1 and store.ledger_entries()[0]['currency']=='USD'


def test_legacy_ledger_rows_gain_immutable_po_binding_without_losing_posted_history(store):
    original=record(store,'legacy-mail');store.approve(original['invoice_id'],'Human reviewer');store.post(original['invoice_id'])
    entry=store.ledger_entries()[0];history=store.actions(original['invoice_id'])
    with store._db() as db:db.execute('ALTER TABLE ledger DROP COLUMN po_number')
    upgraded=APStore(store.path)
    assert upgraded.ledger_entries()[0]==entry and upgraded.actions(original['invoice_id'])==history
    next_invoice=record(upgraded,'new-mail',invoice_number='NEW-AFTER-MIGRATION')
    assert next_invoice['status']=='needs_review' and 'purchase order remaining balance exceeded' in next_invoice['issues']
