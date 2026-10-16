# Working rules

Keep this a standalone synthetic Google ADK/Gemini accounts payable application. Never use credentials, real invoices or production accounting records in fixtures. No model approval/posting tools. No retired or guessed Firedrill integration; wait for the verified new release.

## Entry points and ownership

- `__main__.py`: console/module JSON CLI; `service.py`: sync/async source processing, durable owner, deadline and receipt boundary.
- `agent.py`: actual source-only ADK runner and owned session/model/client cleanup. `transport.py`: pre-normalization HTTP completion and original usage evidence. No global model/client changes or vendor patches.
- `schemas.py`: strict facts, source phrase coverage and exact six-currency minor-unit conversion. Normalized occurrence is not semantic entailment. Human source review remains required.
- `store.py`: private SQLite state/migration, immutable source/facts, current vendor/PO assessment, named review fingerprint approval and immutable PO-bound ledger. A fingerprint is state binding, not authentication.
- `datasets.py`, `data`, `scripts/generate_data.py`: twelve hash-bound cases, exclusive private copies and honest human-import history. Focus, expected outcomes and directory/prior decisions must not enter extraction prompts. Never label imported business state as provider trials.
- `evidence.py`: private atomic receipts, raw HTTP and SDK evidence kept separate, actual final SQLite state even on errors. Missing counters/cost are unknown, never synthesized.

Recheck remaining PO balance, canonical duplicate identity, vendor/currency and exact approved binding under the posting transaction. Preserve old ledger/actions during migration; no silent merging/deletion of conflicting historical rows. Require strict safe integer amounts and revisions; do not coerce booleans/floats. Full-order matching only; no partial billing implied.

One durable extraction lease per source. Never reclaim on age, release another token or overwrite existing facts. Exact process/post retries inspect original committed state and are not new model trials. Manual stopped-owner recovery requires inspection, an exact token and the explicit `--owner-stopped` assertion. Do not blindly retry after uncertain publication. Keep transaction deadline/cancellation guards and no detached write workers. Closing observation cannot imply rollback of already committed work.

## Verification and claims

Use the locked Python 3.12 environment. Run deterministic generation check, relevant pytest cases, build and `scripts/check_distribution.py` before delivery. Package all data; verify both fresh wheel and sdist outside checkout. Keep `.runs`, databases, `.env`, build artifacts and credentials out of Git. CI is offline. Controlled real SDK/HTTP or injected-model tests are not live Gemini proof. Preserve failure/usage/cleanup evidence and document external gates in PLAN.md.
