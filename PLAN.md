# Accounts payable requirement audit

## Completed independent work

1. Reproduced two distinct invoices posting 25000 against one 12500 PO. Bound each ledger row to its original PO, recheck remaining balance/vendor/currency inside approval/post transactions, and preserve migrated legacy history. Strict safe integer facts/PO amounts reject bools/floats.
2. Reproduced case/dash invoice variants posting via different POs. Added canonical vendor/invoice identity with ambiguous directory rejection, immutable source/fact hashes and current review fingerprints. Approval snapshots include source/facts, vendor/PO revisions and prior PO ledger. Changed state invalidates approval; exact post recovery adds no row/action.
3. Reproduced native ADK accepting typed facts with unfinished/absent completion. Actual owned ADK/Gemini HTTP now requires one STOP text candidate before parsing. Original bodies/unknown fields/IDs/status/usage survive success and failure; no retries, one generation, explicit Developer API config and bounded deadline/request/output. Scoped session/runner/SDK/HTTP cleanup runs on startup errors, cancellation and deadlines.
4. Moved exact money conversion into application code: model quotes decimal total, six explicit ISO currency exponents, no rounding or float arithmetic, safe range. Normalized source coverage clears unsupported facts/dates and flags review; source occurrence does not prove semantic correctness.
5. Added durable per-source extraction owners, source/token rechecks and cancellation/deadline guards before commit. Concurrent same-source work fails before a second model; other sources proceed. Recording/commit acknowledgement faults retain actual state and require inspection, never a blind mutation retry. Cached facts are model-free recovery.
6. Added twelve deterministic hash-bound cases/four profiles, including coherent busy business history and exact currency/duplicate/unknown/mismatch/instruction/recovery boundaries. New databases are exclusive/private; imported human rows have no provider claims. Actual native busy prompt includes only the selected source.
7. Added separate JSON CLI review/approval/post commands, exact stopped-owner recovery, real loopback SDK/error/SIGINT/SIGTERM checks, pinned direct dependencies and bundled data. CI checks deterministic generation, all tests and fresh locked wheel/sdist console/module usage outside checkout.

## External gates parked

- Owner-supplied Gemini model/key/budget and human review of actual extraction quality/behavior. Offline controlled SDK/HTTP checks are not live model results.
- New official Firedrill release/contract, followed by the thin integration and persisted ordinary, negative, recovery and campaign evidence. Never use the retired interface or invent replacement APIs.
- Publication, licensing and any real accounting/identity/access/retention decisions. This private synthetic example moves no money.

Continue the portfolio's independent work while these inputs are pending.
