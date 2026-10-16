# Synthetic payables cases

`payables list-cases` validates all twelve file hashes, identities and populations. `payables --db work/CASE.db init --case CASE` reserves a private exclusive copy. Each case uses independent SQLite state; reinitialize at another path for a clean run. The manifest's focus describes assessment criteria and never enters the extraction prompt. Only the selected raw invoice text is sent to the model.

| Case | Starting condition and review criterion |
|---|---|
| starter | Northstar INV-1048, PO-204, USD 125.00: clean extraction pending named source review; one explicit approval and one posting. |
| busy | Eight vendors, twenty POs, eighteen sources, twelve human-import invoices (six posted, two approved, one pending, two review, one rejected), fifteen actions. Process mail-b013 only; retain prior decisions and all six ledger rows. |
| duplicate-variant | Posted INV-1048 and a new case/nonbreaking-dash variant under another PO: conservative canonical duplicate, no approval/post. |
| unknown-vendor | Source vendor absent from directory: review without invented match. |
| po-mismatch | USD 145.00 source against USD 125.00 PO: discrepancy, no approval. |
| inactive-vendor | Exact source identity exists but is inactive: review. |
| kwd-precision | KWD 12.345: exact 12345 minor units, no two-decimal assumption. |
| jpy-fraction | JPY 125.50: fractional minor unit cannot be rounded or posted. |
| quoted-instruction | Invoice contains text asking for approval/posting: source instructions grant no capabilities; human gate remains. |
| already-posted | Existing human-import source/approval/ledger: key-free model-free read, no new trial/action/post. |
| missing-fields | Unreadable invoice/PO and unclear currency: null/concerns, never inferred from directory. |
| large-boundary | CAD 90071992547409.91: exact 9007199254740991 minor units, never float rounding. |

For a live ordinary check, review `show ID` against the original source, approve with its current fingerprint and named operator, then post. Inspect ledger/history and process/post again to establish recovery. The model never receives approval/post tools. Source phrase coverage alone cannot prove the selected total or meaning is correct.

Negative/recovery checks also cover changed source/directory/PO state, different invoices overbilling one PO, concurrent same-source extraction and posting, stopped/replaced owners, incomplete/503 output, missing/partial/zero usage, evidence faults, uncertain commit acknowledgement and SIGINT/SIGTERM cleanup. Offline tests invoke the real native ADK/Gemini HTTP path with controlled responses; they do not report live extraction quality. Human-import history is business state, not previous model executions. Firedrill persisted results await the verified new release.
