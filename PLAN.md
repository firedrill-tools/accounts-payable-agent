# Accounts payable requirement audit

## Corrected first ledger gaps

- Two different invoice numbers each matched and posted the full USD 12500-minor-unit PO-204, producing 25000 posted against a 12500 order. Assessment now sums the immutable PO-bound ledger, and approval/post recheck remaining balance. Concurrent approved invoices can post only within the PO total; the loser returns to review with approval cleared.
- Ledger rows now retain po_number at posting. Legacy rows are backfilled from their immutable invoice link in a serialized migration, preserving timestamps/actions. A changed PO vendor/currency after prior posting requires review instead of mixing budgets across identities.
- InvoiceFacts accepted true as 1 and 12500.0 as 12500. Facts now forbid extra fields and require safe, strict integer minor units; PO admin totals and record_facts revalidation retain that boundary. These checks do not establish extraction accuracy.

## Next independent work

1. Full source/fact/currency/date/vendor/PO schemas, exact minor-unit conversion and current identity/duplicate rules, private SQLite state and coherent immutable approval/ledger evidence. Audit source idempotency/reprocessing and uncertain post recovery.
2. Actual native ADK/Gemini HTTP completion, raw usage/failure evidence, explicit Developer API model/key/endpoint, scoped SDK/model/runner/session/client ownership, cancellation/timeout/request bounds and cleanup. Current runner tests use an injected BaseLlm stub; the model must remain unable to approve or post.
3. Hash-bound starter/busy/ambiguous/boundary invoice/vendor/PO/ledger state, named human import history, private exclusive initialization/reset and task-only model context. No fake provider usage or quality results.
4. Actual CLI HTTP/error/signal proof, fresh locked wheel/sdist, exact-head CI and catalog record.

## External gates parked

Owner-supplied Gemini model/key/budget and human extraction review, new official Firedrill release/persisted ordinary and negative/recovery scenarios, publication and license choice. All data and ledger actions stay synthetic; this app does not move money or use production accounting records. Continue independent implementation.
