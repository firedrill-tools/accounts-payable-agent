# Working rules

Keep this a standalone synthetic accounts payable application. Do not put provider credentials or real invoices in fixtures. ADK extracts facts; SQLite code owns vendor matching, approval and ledger changes. The model must never directly approve or post. Preserve source idempotency, duplicate invoice protection and rechecks after approval. Never describe a stub-model test as a live Gemini proof. Add Firedrill only against the verified new release, never the retired interface.
