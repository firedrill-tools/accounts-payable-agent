# Firedrill agent-first integration

The optional toolchain pins released `@firedrill-run/cli` and `@firedrill-run/sdk` **2.2.2** (October 19, 2026), Node 22+. The app retains its locked native provider/framework dependencies.

## Prepare and run

From the repository root:

```sh
uv sync --locked --extra test
npm ci --prefix firedrill --ignore-scripts --no-audit --no-fund
npm test --prefix firedrill
node firedrill/node_modules/@firedrill-run/cli/bin/firedrill.mjs validate firedrill/scenario.json --json --non-interactive
```

Validation is offline. In a human terminal, use the same CLI `init` to authorize/select the actual project/environment. Automation uses protected `FIREDRILL_TOKEN`, `FIREDRILL_PROJECT_ID`, `FIREDRILL_ENVIRONMENT_ID`. Model operations independently need `GOOGLE_API_KEY` and an enabled `GEMINI_MODEL` or recipe model parameter. Do not put credentials in recipes.

After scope and provider budget are authorized:

```sh
node firedrill/simulate.mjs --preview
node firedrill/simulate.mjs
```

The bounded sequential recipe invokes actual `payables process` and its normal source-only ADK/Gemini extraction, durable owner and receipt boundary. Starter and unknown-vendor cases use native extraction; busy inspection reads actual SQLite history without inference. Approval, rejection, posting, vendor registration and lease release are not adapter actions. Named human review with an exact current fingerprint and the existing ledger transaction guards remain separate. Facts/directory matching are not human approval or semantic accuracy proof; all invoices/ledger effects remain fictional local data.

Each invocation first retains private `.runs/firedrill/attempt-*/invocation.json` with outer run/attempt/scenario identity, then creates a new private DB. Earlier attempts are preserved, not reset. The command adapter validates current ID-only invocation, explicit reviewed input and optional model; it rejects controller/assessment data and unsupported Tool bindings. Operator/CI credentials are removed before application dispatch. Failed actual CLI stdout/stderr and the run directory remain evidence, with a nonzero exit. Local paths are not uploaded artifacts. Existing native deadline, cancellation, owned client/session cleanup and state guards remain in effect; local cleanup does not establish remote cancellation/billing completion or rollback. Inspect receipts and actual state after uncertainty; do not blindly retry writes. Inspection returns the actual application snapshot and is labeled `application_state_inspection`, with no model-execution claim.

## MCP and recovery

Configure remote OAuth operator MCP at **https://mcp.firedrill.run/mcp** using [host setup](https://docs.firedrill.run/guides/coding-agents). MCP prepares, queues and inspects; the local SDK/CLI executes the command target. Host OAuth and execution login are separate. Use `--run BATCH_ID` to attach queued work, `--resume OPERATION_ID` after uncertainty, and `--result BATCH_ID` to read retained results. Preserve SDK journals and local evidence. These scripts retain public SDK diagnostics and whole-batch exit status; a new simulation starts new work.

CI and the fresh locked source archive run offline integration tests with actual private business state and the native missing-key boundary. They establish wiring and cleanup, not live model quality, platform persistence, or uploaded captures. The observation recipe defines no grader/gate. Authenticated persisted happy/negative runs, paid model quality/cost, publication and licensing remain separate gates.

References: [SDK](https://docs.firedrill.run/sdk/typescript), [command targets](https://docs.firedrill.run/guides/callbacks), [operator MCP](https://docs.firedrill.run/guides/control-mcp), [CI](https://docs.firedrill.run/guides/ci).
