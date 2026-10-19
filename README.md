# Accounts payable assistant

A standalone synthetic invoice desk built with [Google ADK](https://adk.dev/) and Gemini. The model extracts facts from one inbox item. SQLite code matches vendors and purchase orders; a named human reviews the original source and approves before a separate command can post to the synthetic ledger. This application does not transfer money or connect to accounting services.

## Start with an offline case

Python 3.12+ and `uv` are required. No provider key is needed to list, initialize or inspect cases.

```sh
uv sync --locked --extra test
uv run --locked payables list-cases
uv run --locked payables --db work/starter.db init --case starter
uv run --locked payables --db work/starter.db source mail-1048
uv run --locked payables --db work/starter.db snapshot
```

Initialization reserves a new private database and refuses an existing destination. Reset by initializing another path. Twelve hash-bound cases cover starter, busy, ambiguous and boundary profiles. The busy desk has eight vendors, twenty POs, eighteen inbox sources, twelve prior human-import invoices, six ledger entries and fifteen operator actions. These are imported business records, with no invented provider executions or usage. See [scenarios](scenarios/README.md).

## Extract, review, approve and post

Export `GOOGLE_API_KEY` and `GEMINI_MODEL` for a Gemini Developer API model enabled on your account. `.env.example` lists configuration; the CLI does not load dotenv files. Live extraction sends the selected synthetic invoice text to that provider and may incur charges.

```sh
uv run --locked payables --db work/starter.db process --case starter
uv run --locked payables --db work/starter.db show 1
```

`show` includes the source, extracted facts, current vendor/PO state, issues and `review_fingerprint`. Inspect the amount, identities and dates against the source. Copy the fingerprint into the approval command:

```sh
uv run --locked payables --db work/starter.db approve 1 --actor ap-reviewer --review-token '<review_fingerprint>'
uv run --locked payables --db work/starter.db post 1
uv run --locked payables --db work/starter.db ledger
uv run --locked payables --db work/starter.db history 1
```

Approval requires the current fingerprint and a clean match. It binds the exact source/facts and vendor/PO revisions; posting rechecks that binding and remaining balance inside its transaction. A changed directory or PO requires review again. The actor name and fingerprint are a local review workflow, not identity authentication or proof someone read the invoice. Model tools cannot approve or post.

Repeated `process` on an existing source returns its original invoice without a key or model call; the receipt labels it `model_free_existing_invoice`. Repeated `post` returns `posting_replayed: true` without another ledger row or action. Neither is a new model trial.

## State and commands

`payables` and `python -m accounts_payable` expose the same JSON CLI. Global `--db PATH` precedes the command. Additional commands:

- `seed`: idempotently add the original two demo vendors/POs without resetting edits.
- `ingest SOURCE_ID FILE`: bounded UTF-8 text; exact source replay is safe, changed content under the same ID fails.
- `process SOURCE_ID`: extract another ingested source. Options: `--model`, `--base-url`, `--timeout` (maximum 120 seconds), `--output-root`.
- `list`, `source SOURCE_ID`, `show ID`, `ledger`, `history ID`, `snapshot`: inspect business state.
- `reconcile ID`, `reject ID --actor NAME`: refresh matching or record a human rejection.
- `add-vendor ID NAME [--inactive]`, `set-vendor-active ID true|false`, `set-po PO VENDOR_ID CURRENCY TOTAL_MINOR [--inactive]`: explicit local directory changes.
- `leases`: inspect extraction owners and attempts. After stopping an owner and inspecting its receipt/state, `release-owner SOURCE_ID --token TOKEN --owner-stopped` releases only that exact owner. There is no age-based takeover or automatic uncertain-write retry.

A source has one durable extraction owner. Other sources can proceed independently; cases use separate databases. Facts, source hashes and original ledger/approval history remain immutable through the application. Existing databases migrate without inventing dataset or model history. New databases/receipts are mode 0600 and new private directories 0700; existing parent permissions are preserved. SQLite WAL/SHM inherit database privacy. Receipts contain synthetic invoice text and model output; protect the working directory.

## Matching and money

Missing/unsupported fields, concerns, unknown/inactive/ambiguous vendors, inactive/missing POs, vendor/currency/amount discrepancies and duplicates require review. Unicode NFKC, case, whitespace and dash normalization make invoice/vendor identity conservative; originals remain visible. Canonically ambiguous directory names cannot match silently, and case/dash variants of the same invoice cannot post through another PO.

The model quotes `total_text`; application code computes strict integer minor units with exact decimal arithmetic. Supported ISO 4217 subset: USD/CAD/EUR/GBP two decimal places, JPY zero, KWD three. Fractional minor units, malformed comma grouping, floats/booleans and values above 2^53−1 are rejected. Current matching requires the full PO amount; partial billing, tax allocation, credit notes and exchange rates are not implemented. Every ledger row retains its original PO; two different invoice numbers cannot each bill the full order. A changed PO cannot mix prior vendor/currency identities.

Model fields require normalized phrase coverage in the selected source, including due dates. Unsupported fields are cleared and flagged. Phrase occurrence does not establish semantic entailment or identify which printed amount is the total; human source review remains required. A source containing instructions cannot grant approval or posting authority.

## Runtime and observations

The normal path uses the actual ADK runner and Gemini Developer API SDK with owned clients and sessions. Only one nonstreaming generation is allowed, with one STOP text candidate, no tools/media/executable content and at most 4096 output tokens. Retries are disabled; phase/connect limits are 30/five seconds and overall processing at most 120 seconds. HTTPS or explicit loopback HTTP endpoints are supported; ambient Vertex/enterprise selection is rejected. Output schema extras and unfinished/blocked/malformed responses cannot commit facts.

Private `.runs/UUID/receipt.json` files retain source/state hashes, fixture identity, exact original HTTP observations, response/model IDs, SDK events, raw and computed facts, ownership, lifecycle and actual final business state. Original missing/partial/invalid/zero usage stays distinct from SDK-normalized defaults; totals are not guessed and cost remains unknown. Request size is checked before dispatch; response evidence is capped at 1 MiB after buffering, not a memory or compressed-wire limit. Recording faults stop subsequent work. An exception after a committed write reports actual state and does not imply rollback; inspect `actual_invoice`, `facts_recorded_by_run` and `leases` before recovery. If final recording fails, the last file may remain running and the exception carries in-memory evidence.

Cancellation/deadline cleanup deletes the owned session and closes runner, SDK and supplied HTTP client before releasing its exact lease. Facts commit has cancellation/deadline checks inside the SQLite transaction. A completed commit cannot be undone by later cancellation. The native HTTP client owns a supplied test transport once constructed; caller-injected `BaseLlm` models retain their own clients and are labeled separately. See [COMPATIBILITY.md](COMPATIBILITY.md) for the exercised contract.

## Verify and remaining gates

```sh
uv run --locked python scripts/generate_data.py --check
uv run --locked --extra test pytest -q
uv build
uv run --locked python scripts/check_distribution.py
```

Offline checks exercise actual ADK/Gemini HTTP over controlled transports and real CLI loopback servers, concurrent ownership/posting, cancellation/signals, recording faults, immutable review/ledger state, all twelve fixtures and fresh locked wheel/sdist installs outside the checkout. They do not establish live Gemini accuracy, billing or real financial behavior. No credentials or production records belong in fixtures.

Live extraction quality requires owner-supplied model/key/budget and human review. The new released Firedrill interface and its persisted ordinary, negative and recovery runs remain external gates. No retired API or guessed integration is included. [PLAN.md](PLAN.md) tracks completed work and those gates. This repository remains private pending publication and license decisions.

Explore the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog), [Firedrill](https://firedrill.run/) and [official developer documentation](https://docs.firedrill.run/). Currency exponents use the [SIX ISO 4217 list](https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml), published 2026-09-17; the source digest and qualified SDK versions are recorded in [COMPATIBILITY.md](COMPATIBILITY.md).

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks exercise actual state and native key preflight; authenticated persisted runs and live quality/cost remain separate gates.
