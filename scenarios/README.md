# Scenario set

The seed data uses two synthetic vendors, Northstar Paper Supply and Orbit Facilities. `PO-204` belongs to Northstar for USD 125.00; `PO-208` belongs to Orbit for USD 850.00.

1. **Ordinary:** ingest `northstar-1048.txt`, process with Gemini, verify `pending_approval`, approve as a named operator, then post exactly one USD 125.00 ledger entry.
2. **Duplicate delivery:** ingest `northstar-1048-duplicate.txt` with another source ID. It must become `duplicate`, and approval or posting must fail.
3. **Unknown vendor:** ingest `unknown-vendor.txt`. It must need review; adding the vendor and corresponding PO then reconciling can make it ready for approval.
4. **Amount discrepancy:** ingest `discrepancy.txt`. The PO amount mismatch must need review. A PO changed after an approval must be checked again before posting.
5. **Missing or ambiguous facts:** null totals, invoice numbers, currencies or model concerns must not reach approval.

Each case uses a fresh database for isolation. Fixture text is synthetic and does not contain provider credentials. Tests use a local ADK model stub and exercise these state transitions without spending provider credits.
