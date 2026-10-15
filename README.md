# Accounts payable assistant

A synthetic invoice desk using [Google ADK](https://google.github.io/adk-docs/) and Gemini to extract invoice facts. A local SQLite directory matches vendors and purchase orders. Every matching invoice requires a named human approval before it can enter the synthetic ledger. The model cannot perform approval or posting.

## Run

Requires Python 3.12+ and `uv`:

```sh
uv sync --extra test
uv run pytest -q
uv run python -m accounts_payable --db ap.db seed
uv run python -m accounts_payable --db ap.db ingest mail-1048 fixtures/northstar-1048.txt
```

Set `GOOGLE_API_KEY` and `GEMINI_MODEL` to values enabled for your account, then process the inbox item and inspect the returned invoice ID:

```sh
uv run python -m accounts_payable --db ap.db process mail-1048
uv run python -m accounts_payable --db ap.db list
uv run python -m accounts_payable --db ap.db approve 1 --actor ap-reviewer
uv run python -m accounts_payable --db ap.db post 1
uv run python -m accounts_payable --db ap.db ledger
uv run python -m accounts_payable --db ap.db history 1
```

The other commands are `show ID`, `reject ID --actor NAME`, `reconcile ID`, `add-vendor ID NAME`, and `set-po PO_NUMBER VENDOR_ID CURRENCY TOTAL_MINOR`. Amounts use integer minor units; USD 125.00 is `12500`. `seed` is idempotent. The state file is ignored by Git.

## Rules and limits

- Replaying the same source ID with identical text is safe; reusing it with different text is rejected. A second source containing the same vendor and invoice number is marked `duplicate` and cannot be approved.
- Unknown or inactive vendors, missing or inactive purchase orders, amount or currency mismatches, missing fields and extraction concerns require review. `reconcile` checks an invoice again after a vendor or PO is corrected.
- Approval requires a named operator and a current exact match. Posting checks the match again, so a PO changed after approval stops the post and clears approval. A rejected invoice cannot post. The ledger has unique invoice and vendor/invoice keys.
- The database holds source invoice text and operator decisions. This example uses synthetic text only; adapt data retention, access control and audit requirements before using real financial records.

`uv run pytest -q` exercises an ADK runner with a local model stub plus the inbox, duplicate, mismatch, recovery and approval gates. It does not establish Gemini extraction accuracy or a live provider result. Those checks require provider access and measured output. Firedrill integration remains pending until the new released interface is qualified. Dependencies are locked in `uv.lock`.
