"""Private synthetic AP state; source-bound facts, human review and immutable posting."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .schemas import (
    InvoiceFacts,
    amount,
    bounded_text,
    canonical,
    currency,
    identifier,
    identity,
    parse_amount,
    row_id,
    sha,
)


def now():
    return datetime.now(timezone.utc).isoformat()


FACT_FIELDS = (
    "vendor_name",
    "invoice_number",
    "po_number",
    "currency",
    "total_minor",
    "total_text",
    "due_date",
    "concerns",
)


class APStore:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS inbox(source_id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vendors(vendor_id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE,active INTEGER NOT NULL CHECK(active IN (0,1)));
                CREATE TABLE IF NOT EXISTS purchase_orders(po_number TEXT PRIMARY KEY,vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),currency TEXT NOT NULL,total_minor INTEGER NOT NULL CHECK(total_minor>0),active INTEGER NOT NULL CHECK(active IN (0,1)));
                CREATE TABLE IF NOT EXISTS invoices(invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL UNIQUE REFERENCES inbox(source_id),vendor_name TEXT,vendor_id TEXT REFERENCES vendors(vendor_id),invoice_number TEXT,po_number TEXT,currency TEXT,total_minor INTEGER,due_date TEXT,concerns_json TEXT NOT NULL,issues_json TEXT NOT NULL,status TEXT NOT NULL,approval_actor TEXT,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger(entry_id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(invoice_id),vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),invoice_number TEXT NOT NULL,currency TEXT NOT NULL,total_minor INTEGER NOT NULL,posted_at TEXT NOT NULL,UNIQUE(vendor_id,invoice_number));
                CREATE TABLE IF NOT EXISTS actions(action_id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id),action TEXT NOT NULL,actor TEXT NOT NULL,at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS extraction_leases(source_id TEXT PRIMARY KEY REFERENCES inbox(source_id),token TEXT NOT NULL UNIQUE,run_id TEXT NOT NULL,owner_pid INTEGER NOT NULL,started_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS extraction_attempts(run_id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES inbox(source_id),token TEXT NOT NULL,receipt_path TEXT NOT NULL,source_sha256 TEXT NOT NULL,origin TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS dataset_metadata(id INTEGER PRIMARY KEY CHECK(id=1),identity_json TEXT,fixture_sha256 TEXT NOT NULL);
            """)
            columns = {
                "vendors": {
                    "canonical_name_key": "TEXT",
                    "revision": "INTEGER NOT NULL DEFAULT 1",
                },
                "purchase_orders": {"revision": "INTEGER NOT NULL DEFAULT 1"},
                "invoices": {
                    "total_text": "TEXT",
                    "source_sha256": "TEXT",
                    "facts_sha256": "TEXT",
                    "facts_origin": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
                    "extraction_run_id": "TEXT",
                    "invoice_key": "TEXT",
                    "approval_fingerprint": "TEXT",
                    "approval_binding_json": "TEXT",
                },
                "ledger": {
                    "po_number": "TEXT",
                    "invoice_key": "TEXT",
                    "source_sha256": "TEXT",
                    "facts_sha256": "TEXT",
                    "approval_fingerprint": "TEXT",
                    "binding_json": "TEXT",
                },
                "actions": {
                    "binding_json": "TEXT",
                    "origin": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
                },
            }
            for table, additions in columns.items():
                existing = {
                    r["name"] for r in db.execute(f"PRAGMA table_info({table})")
                }
                for name, definition in additions.items():
                    if name not in existing:
                        db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
            for vendor in db.execute("SELECT vendor_id,name FROM vendors").fetchall():
                db.execute(
                    "UPDATE vendors SET canonical_name_key=? WHERE vendor_id=?",
                    (identity(vendor["name"]), vendor["vendor_id"]),
                )
            for row in db.execute("SELECT * FROM invoices").fetchall():
                inv = self._invoice(row)
                digest = sha(canonical(self._facts(inv)).encode())
                source = db.execute(
                    "SELECT sha256 FROM inbox WHERE source_id=?", (inv["source_id"],)
                ).fetchone()
                db.execute(
                    "UPDATE invoices SET invoice_key=?,facts_sha256=COALESCE(facts_sha256,?),source_sha256=COALESCE(source_sha256,?) WHERE invoice_id=?",
                    (
                        identity(inv["invoice_number"])
                        if inv["invoice_number"]
                        else None,
                        digest,
                        source["sha256"] if source else None,
                        inv["invoice_id"],
                    ),
                )
            for entry in db.execute("SELECT * FROM ledger").fetchall():
                invoice = db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (entry["invoice_id"],)
                ).fetchone()
                db.execute(
                    "UPDATE ledger SET po_number=COALESCE(po_number,?),invoice_key=COALESCE(invoice_key,?),source_sha256=COALESCE(source_sha256,?),facts_sha256=COALESCE(facts_sha256,?) WHERE entry_id=?",
                    (
                        invoice["po_number"] if invoice else None,
                        identity(entry["invoice_number"]),
                        invoice["source_sha256"] if invoice else None,
                        invoice["facts_sha256"] if invoice else None,
                        entry["entry_id"],
                    ),
                )

    @contextmanager
    def _db(self, commit_guard=None):
        if commit_guard:
            commit_guard()
        db = sqlite3.connect(self.path, isolation_level=None, timeout=0.1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=100")
        try:
            yield db
            if db.in_transaction and commit_guard:
                commit_guard()
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _invoice(row):
        if row is None:
            return None
        result = dict(row)
        result["concerns"] = json.loads(result.pop("concerns_json"))
        result["issues"] = json.loads(result.pop("issues_json"))
        return result

    @staticmethod
    def _facts(invoice):
        return {key: invoice.get(key) for key in FACT_FIELDS}

    def add_vendor(self, vendor_id, name, active=True):
        identifier(vendor_id, "vendor ID")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or type(active) is not bool
        ):
            raise ValueError("bounded vendor name and boolean active flag required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO vendors(vendor_id,name,name_key,active,canonical_name_key,revision) VALUES(?,?,?,?,?,1)",
                (
                    vendor_id,
                    name.strip(),
                    " ".join(name.casefold().split()),
                    int(active),
                    identity(name),
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)
                ).fetchone()
            )

    def set_vendor_active(self, vendor_id, active):
        identifier(vendor_id, "vendor ID")
        if type(active) is not bool:
            raise ValueError("active must be boolean")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            vendor = db.execute(
                "SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)
            ).fetchone()
            if vendor is None:
                raise KeyError(vendor_id)
            if bool(vendor["active"]) != active:
                db.execute(
                    "UPDATE vendors SET active=?,revision=revision+1 WHERE vendor_id=?",
                    (int(active), vendor_id),
                )
            return dict(
                db.execute(
                    "SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)
                ).fetchone()
            )

    def set_po(self, po_number, vendor_id, code, total_minor, active=True):
        identifier(po_number, "PO number")
        identifier(vendor_id, "vendor ID")
        currency(code)
        amount(total_minor, positive=True)
        if type(active) is not bool:
            raise ValueError("active must be boolean")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM purchase_orders WHERE po_number=?", (po_number,)
            ).fetchone()
            values = (vendor_id, code, total_minor, int(active))
            if prior is None:
                db.execute(
                    "INSERT INTO purchase_orders(po_number,vendor_id,currency,total_minor,active,revision) VALUES(?,?,?,?,?,1)",
                    (po_number, *values),
                )
            elif values != tuple(
                prior[key] for key in ("vendor_id", "currency", "total_minor", "active")
            ):
                db.execute(
                    "UPDATE purchase_orders SET vendor_id=?,currency=?,total_minor=?,active=?,revision=revision+1 WHERE po_number=?",
                    (*values, po_number),
                )
            return dict(
                db.execute(
                    "SELECT * FROM purchase_orders WHERE po_number=?", (po_number,)
                ).fetchone()
            )

    def seed_demo(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for vendor_id, name in [
                ("vendor-northstar", "Northstar Paper Supply"),
                ("vendor-orbit", "Orbit Facilities"),
            ]:
                db.execute(
                    "INSERT OR IGNORE INTO vendors(vendor_id,name,name_key,active,canonical_name_key,revision) VALUES(?,?,?,1,?,1)",
                    (vendor_id, name, identity(name), identity(name)),
                )
            for po, vendor, total in [
                ("PO-204", "vendor-northstar", 12500),
                ("PO-208", "vendor-orbit", 85000),
            ]:
                db.execute(
                    "INSERT OR IGNORE INTO purchase_orders(po_number,vendor_id,currency,total_minor,active,revision) VALUES(?,?,'USD',?,1,1)",
                    (po, vendor, total),
                )

    def ingest(self, source_id, body):
        identifier(source_id, "source ID")
        bounded_text(body)
        digest = sha(body.encode())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM inbox WHERE source_id=?", (source_id,)
            ).fetchone()
            if prior:
                if prior["sha256"] != digest or prior["body"] != body:
                    raise ValueError("source ID reused for different invoice content")
                return {"source_id": source_id, "sha256": digest, "replayed": True}
            db.execute("INSERT INTO inbox VALUES(?,?,?)", (source_id, body, digest))
            return {"source_id": source_id, "sha256": digest, "replayed": False}

    def _source(self, db, source_id):
        row = db.execute(
            "SELECT * FROM inbox WHERE source_id=?", (source_id,)
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        source = dict(row)
        bounded_text(source["body"])
        if sha(source["body"].encode()) != source["sha256"]:
            raise ValueError("source bytes do not match the stored hash")
        return source

    def source_snapshot(self, source_id):
        identifier(source_id, "source ID")
        with self._db() as db:
            return self._source(db, source_id)

    def source_text(self, source_id):
        return self.source_snapshot(source_id)["body"]

    def _verify(self, db, invoice):
        source = self._source(db, invoice["source_id"])
        digest = sha(canonical(self._facts(invoice)).encode())
        if (
            source["sha256"] != invoice["source_sha256"]
            or digest != invoice["facts_sha256"]
        ):
            raise ValueError(
                "source or immutable invoice facts changed; original review is invalid"
            )
        return source

    def _assess(self, db, invoice):
        issues = list(invoice["concerns"])
        vendor_id = None
        try:
            InvoiceFacts.model_validate(self._facts(invoice))
        except ValueError:
            issues.append("recorded facts violate the current schema")
        if not invoice["vendor_name"]:
            issues.append("missing vendor")
        else:
            matches = db.execute(
                "SELECT * FROM vendors WHERE canonical_name_key=? ORDER BY vendor_id",
                (identity(invoice["vendor_name"]),),
            ).fetchall()
            if not matches:
                issues.append("unknown vendor")
            elif len(matches) != 1:
                issues.append("ambiguous vendor identity")
            elif not matches[0]["active"]:
                issues.append("inactive vendor")
            else:
                vendor_id = matches[0]["vendor_id"]
        if not invoice["invoice_number"]:
            issues.append("missing invoice number")
        if invoice["currency"] not in ("USD", "CAD", "EUR", "GBP", "JPY", "KWD"):
            issues.append("missing or unsupported currency")
        if invoice["total_minor"] is None or invoice["total_minor"] <= 0:
            issues.append("missing or nonpositive total")
        if invoice.get("total_text") is not None and invoice["total_minor"] is not None:
            try:
                if (
                    parse_amount(invoice["total_text"], invoice["currency"])
                    != invoice["total_minor"]
                ):
                    issues.append("printed total and minor-unit amount disagree")
            except ValueError as error:
                issues.append(str(error))
        if not invoice["po_number"]:
            issues.append("missing purchase order")
        else:
            po = db.execute(
                "SELECT * FROM purchase_orders WHERE po_number=?",
                (invoice["po_number"],),
            ).fetchone()
            if po is None:
                issues.append("unknown purchase order")
            elif not po["active"]:
                issues.append("inactive purchase order")
            else:
                if vendor_id and po["vendor_id"] != vendor_id:
                    issues.append("purchase order vendor mismatch")
                if invoice["currency"] and po["currency"] != invoice["currency"]:
                    issues.append("purchase order currency mismatch")
                if (
                    invoice["total_minor"] is not None
                    and po["total_minor"] != invoice["total_minor"]
                ):
                    issues.append("purchase order amount mismatch")
                posted = db.execute(
                    "SELECT * FROM ledger WHERE po_number=? AND invoice_id!=?",
                    (po["po_number"], invoice.get("invoice_id", -1)),
                ).fetchall()
                if any(
                    row["vendor_id"] != po["vendor_id"]
                    or row["currency"] != po["currency"]
                    for row in posted
                ):
                    issues.append("purchase order identity changed after prior posting")
                if invoice["total_minor"] is not None and invoice["total_minor"] > po[
                    "total_minor"
                ] - sum(row["total_minor"] for row in posted):
                    issues.append("purchase order remaining balance exceeded")
        if vendor_id and invoice["invoice_number"]:
            duplicate = db.execute(
                "SELECT invoice_id FROM invoices WHERE vendor_id=? AND invoice_key=? AND invoice_id!=? AND status!='duplicate' ORDER BY invoice_id LIMIT 1",
                (
                    vendor_id,
                    identity(invoice["invoice_number"]),
                    invoice.get("invoice_id", -1),
                ),
            ).fetchone()
            if duplicate:
                issues.append(f"duplicate of invoice {duplicate['invoice_id']}")
                return "duplicate", list(dict.fromkeys(issues)), vendor_id
        return (
            ("needs_review" if issues else "pending_approval"),
            list(dict.fromkeys(issues)),
            vendor_id,
        )

    def record_facts(
        self,
        source_id,
        facts,
        *,
        expected_source_sha=None,
        origin="human_import",
        run_id=None,
        lease_token=None,
        commit_guard=None,
    ):
        identifier(source_id, "source ID")
        facts = InvoiceFacts.model_validate(
            facts.model_dump() if isinstance(facts, InvoiceFacts) else facts
        )
        if origin not in {"human_import", "model_extraction", "caller_injected_model"}:
            raise ValueError("invalid facts origin")
        with self._db(commit_guard) as db:
            db.execute("BEGIN IMMEDIATE")
            source = self._source(db, source_id)
            if (
                expected_source_sha is not None
                and source["sha256"] != expected_source_sha
            ):
                raise ValueError("source changed during extraction")
            lease = db.execute(
                "SELECT * FROM extraction_leases WHERE source_id=?", (source_id,)
            ).fetchone()
            if lease_token is not None:
                if (
                    not lease
                    or lease["token"] != lease_token
                    or lease["run_id"] != run_id
                ):
                    raise ValueError(
                        "extraction ownership changed; refusing stale facts"
                    )
            elif lease:
                raise ValueError("source has an active extractor; inspect its owner")
            if origin != "human_import" and lease_token is None:
                raise ValueError("model facts require their owned extraction attempt")
            prior = db.execute(
                "SELECT * FROM invoices WHERE source_id=?", (source_id,)
            ).fetchone()
            if prior:
                if lease_token is not None:
                    raise ValueError(
                        "facts already exist; inspect original extraction instead of overwriting"
                    )
                invoice = self._invoice(prior)
                self._verify(db, invoice)
                return invoice
            invoice = facts.model_dump(mode="json")
            status, issues, vendor_id = self._assess(db, invoice)
            digest = sha(canonical(invoice).encode())
            columns = [
                "source_id",
                *FACT_FIELDS[:-1],
                "concerns_json",
                "issues_json",
                "status",
                "vendor_id",
                "created_at",
                "source_sha256",
                "facts_sha256",
                "facts_origin",
                "extraction_run_id",
                "invoice_key",
            ]
            values = [
                source_id,
                *[invoice[key] for key in FACT_FIELDS[:-1]],
                json.dumps(invoice["concerns"]),
                json.dumps(issues),
                status,
                vendor_id,
                now(),
                source["sha256"],
                digest,
                origin,
                run_id,
                identity(invoice["invoice_number"])
                if invoice["invoice_number"]
                else None,
            ]
            cursor = db.execute(
                f"INSERT INTO invoices({','.join(columns)}) VALUES({','.join('?' for _ in values)})",
                values,
            )
            if lease_token:
                db.execute(
                    "DELETE FROM extraction_leases WHERE source_id=? AND token=?",
                    (source_id, lease_token),
                )
                db.execute(
                    "UPDATE extraction_attempts SET status='facts_recorded',finished_at=? WHERE run_id=? AND token=?",
                    (now(), run_id, lease_token),
                )
            return self._invoice(
                db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (cursor.lastrowid,)
                ).fetchone()
            )

    def _checked(self, db, invoice_id, allowed):
        row_id(invoice_id)
        invoice = self._invoice(
            db.execute(
                "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
            ).fetchone()
        )
        if invoice is None:
            raise KeyError(invoice_id)
        if invoice["status"] not in allowed:
            raise ValueError(
                f"invoice is {invoice['status']}; expected {', '.join(allowed)}"
            )
        self._verify(db, invoice)
        return invoice

    def _refresh(self, db, invoice):
        status, issues, vendor_id = self._assess(db, invoice)
        db.execute(
            "UPDATE invoices SET status=?,issues_json=?,vendor_id=?,approval_actor=NULL,approval_fingerprint=NULL,approval_binding_json=NULL WHERE invoice_id=?",
            (status, json.dumps(issues), vendor_id, invoice["invoice_id"]),
        )
        return self._invoice(
            db.execute(
                "SELECT * FROM invoices WHERE invoice_id=?", (invoice["invoice_id"],)
            ).fetchone()
        )

    def _review(self, db, invoice):
        source = self._verify(db, invoice)
        status, issues, vendor_id = self._assess(db, invoice)
        vendors = (
            [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM vendors WHERE canonical_name_key=? ORDER BY vendor_id",
                    (identity(invoice["vendor_name"]),),
                )
            ]
            if invoice["vendor_name"]
            else []
        )
        po = (
            db.execute(
                "SELECT * FROM purchase_orders WHERE po_number=?",
                (invoice["po_number"],),
            ).fetchone()
            if invoice["po_number"]
            else None
        )
        prior = (
            [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM ledger WHERE po_number=? AND invoice_id!=? ORDER BY entry_id",
                    (invoice["po_number"], invoice["invoice_id"]),
                )
            ]
            if invoice["po_number"]
            else []
        )
        binding = {
            "schema_version": 1,
            "invoice_id": invoice["invoice_id"],
            "source_id": invoice["source_id"],
            "source_sha256": source["sha256"],
            "facts": self._facts(invoice),
            "facts_sha256": invoice["facts_sha256"],
            "vendors": vendors,
            "purchase_order": dict(po) if po else None,
            "prior_po_ledger": prior,
            "assessment_status": status,
            "issues": issues,
        }
        return {
            "invoice": invoice,
            "source": source,
            "assessment_status": status,
            "issues": issues,
            "binding": binding,
            "review_fingerprint": sha(canonical(binding).encode()),
        }

    def review(self, invoice_id):
        row_id(invoice_id)
        with self._db() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
            ).fetchone()
            if row is None:
                raise KeyError(invoice_id)
            return self._review(db, self._invoice(row))

    def reconcile(self, invoice_id):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._refresh(
                db,
                self._checked(
                    db,
                    invoice_id,
                    ("needs_review", "pending_approval", "approved", "duplicate"),
                ),
            )

    def approve(self, invoice_id, actor, *, expected_review=None, origin="operator"):
        identifier(actor, "approval actor")
        if not isinstance(expected_review, str) or len(expected_review) != 64:
            raise ValueError("current review fingerprint is required for approval")
        if origin not in {"operator", "human_import"}:
            raise ValueError("approval must come from an operator")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._checked(db, invoice_id, ("pending_approval",))
            review = self._review(db, invoice)
            if review["assessment_status"] != "pending_approval":
                return self._refresh(db, invoice)
            if review["review_fingerprint"] != expected_review:
                raise ValueError(
                    "review fingerprint changed; inspect the current source and directory again"
                )
            binding = canonical(review["binding"])
            db.execute(
                "UPDATE invoices SET status='approved',approval_actor=?,approval_fingerprint=?,approval_binding_json=? WHERE invoice_id=?",
                (actor, expected_review, binding, invoice_id),
            )
            db.execute(
                "INSERT INTO actions(invoice_id,action,actor,at,binding_json,origin) VALUES(?,'approved',?,?,?,?)",
                (invoice_id, actor, now(), binding, origin),
            )
            return self._invoice(
                db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                ).fetchone()
            )

    def reject(self, invoice_id, actor, *, origin="operator"):
        identifier(actor, "decision actor")
        if origin not in {"operator", "human_import"}:
            raise ValueError("invalid decision origin")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._checked(
                db,
                invoice_id,
                ("pending_approval", "needs_review", "duplicate", "approved"),
            )
            db.execute(
                "UPDATE invoices SET status='rejected',approval_actor=?,approval_fingerprint=NULL,approval_binding_json=NULL WHERE invoice_id=?",
                (actor, invoice_id),
            )
            db.execute(
                "INSERT INTO actions(invoice_id,action,actor,at,origin) VALUES(?,'rejected',?,?,?)",
                (invoice_id, actor, now(), origin),
            )
            return self._invoice(
                db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                ).fetchone()
            )

    def post(self, invoice_id, *, origin="operator"):
        if origin not in {"operator", "human_import"}:
            raise ValueError("invalid posting origin")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._checked(db, invoice_id, ("approved", "posted"))
            if invoice["status"] == "posted":
                entry = db.execute(
                    "SELECT * FROM ledger WHERE invoice_id=?", (invoice_id,)
                ).fetchone()
                if entry is None or any(
                    entry[key] != invoice[key]
                    for key in (
                        "vendor_id",
                        "invoice_number",
                        "po_number",
                        "currency",
                        "total_minor",
                    )
                ):
                    raise ValueError(
                        "posted invoice does not match its immutable ledger; inspect history"
                    )
                return {**invoice, "posting_replayed": True}
            review = self._review(db, invoice)
            if review["assessment_status"] != "pending_approval":
                return self._refresh(db, invoice)
            if (
                not invoice["approval_fingerprint"]
                or review["review_fingerprint"] != invoice["approval_fingerprint"]
            ):
                refreshed = self._refresh(db, invoice)
                issues = refreshed["issues"] + [
                    "approval snapshot changed; review and approve again"
                ]
                db.execute(
                    "UPDATE invoices SET status='needs_review',issues_json=? WHERE invoice_id=?",
                    (json.dumps(issues), invoice_id),
                )
                return self._invoice(
                    db.execute(
                        "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                    ).fetchone()
                )
            db.execute(
                "INSERT INTO ledger(invoice_id,vendor_id,invoice_number,po_number,currency,total_minor,posted_at,invoice_key,source_sha256,facts_sha256,approval_fingerprint,binding_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    invoice_id,
                    invoice["vendor_id"],
                    invoice["invoice_number"],
                    invoice["po_number"],
                    invoice["currency"],
                    invoice["total_minor"],
                    now(),
                    invoice["invoice_key"],
                    invoice["source_sha256"],
                    invoice["facts_sha256"],
                    invoice["approval_fingerprint"],
                    invoice["approval_binding_json"],
                ),
            )
            db.execute(
                "UPDATE invoices SET status='posted' WHERE invoice_id=?", (invoice_id,)
            )
            db.execute(
                "INSERT INTO actions(invoice_id,action,actor,at,binding_json,origin) VALUES(?,'posted',?,?,?,?)",
                (
                    invoice_id,
                    invoice["approval_actor"],
                    now(),
                    invoice["approval_binding_json"],
                    origin,
                ),
            )
            return {
                **self._invoice(
                    db.execute(
                        "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                    ).fetchone()
                ),
                "posting_replayed": False,
            }

    def claim_extraction(self, source_id, run_id, receipt_path, origin):
        identifier(source_id, "source ID")
        identifier(run_id, "run ID")
        if origin not in {"model_extraction", "caller_injected_model"}:
            raise ValueError("invalid extraction origin")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            source = self._source(db, source_id)
            prior = db.execute(
                "SELECT * FROM invoices WHERE source_id=?", (source_id,)
            ).fetchone()
            if prior:
                invoice = self._invoice(prior)
                self._verify(db, invoice)
                return {"cached": invoice, "source": source, "token": None}
            lease = db.execute(
                "SELECT * FROM extraction_leases WHERE source_id=?", (source_id,)
            ).fetchone()
            if lease:
                raise ValueError(
                    f"source already processing; inspect stopped owner run {lease['run_id']}"
                )
            token = str(uuid.uuid4())
            started = now()
            db.execute(
                "INSERT INTO extraction_leases VALUES(?,?,?,?,?)",
                (source_id, token, run_id, os.getpid(), started),
            )
            db.execute(
                "INSERT INTO extraction_attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    source_id,
                    token,
                    str(receipt_path),
                    source["sha256"],
                    origin,
                    "running",
                    started,
                    None,
                    None,
                ),
            )
            return {"cached": None, "source": source, "token": token}

    def release_extraction(self, source_id, token, error=None):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            lease = db.execute(
                "SELECT * FROM extraction_leases WHERE source_id=?", (source_id,)
            ).fetchone()
            if lease is None or lease["token"] != token:
                return False
            db.execute(
                "DELETE FROM extraction_leases WHERE source_id=? AND token=?",
                (source_id, token),
            )
            db.execute(
                "UPDATE extraction_attempts SET status='failed',finished_at=?,error=? WHERE run_id=? AND token=?",
                (
                    now(),
                    str(error) if error else "owner released before facts commit",
                    lease["run_id"],
                    token,
                ),
            )
            return True

    def find_source_invoice(self, source_id):
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM invoices WHERE source_id=?", (source_id,)
            ).fetchone()
            if row:
                invoice = self._invoice(row)
                self._verify(db, invoice)
                return invoice
            return None

    def get_invoice(self, invoice_id):
        row_id(invoice_id)
        with self._db() as db:
            return self._invoice(
                db.execute(
                    "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)
                ).fetchone()
            )

    def list_invoices(self):
        with self._db() as db:
            return [
                self._invoice(row)
                for row in db.execute("SELECT * FROM invoices ORDER BY invoice_id")
            ]

    def ledger_entries(self):
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM ledger ORDER BY entry_id")
            ]

    def actions(self, invoice_id):
        row_id(invoice_id)
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM actions WHERE invoice_id=? ORDER BY action_id",
                    (invoice_id,),
                )
            ]

    def snapshot(self, *, include_bodies=True):
        with self._db() as db:
            db.execute("BEGIN")
            metadata = db.execute(
                "SELECT * FROM dataset_metadata WHERE id=1"
            ).fetchone()
            sources = [
                dict(r) for r in db.execute("SELECT * FROM inbox ORDER BY source_id")
            ]
            if not include_bodies:
                for source in sources:
                    body = source.pop("body")
                    source.update(body_sha256=sha(body.encode()), body_length=len(body))
            return {
                "schema_version": 2,
                "dataset": json.loads(metadata["identity_json"])
                if metadata and metadata["identity_json"]
                else None,
                "fixture_sha256": metadata["fixture_sha256"] if metadata else None,
                "inbox": sources,
                "vendors": [
                    dict(r)
                    for r in db.execute("SELECT * FROM vendors ORDER BY vendor_id")
                ],
                "purchase_orders": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM purchase_orders ORDER BY po_number"
                    )
                ],
                "invoices": [
                    self._invoice(r)
                    for r in db.execute("SELECT * FROM invoices ORDER BY invoice_id")
                ],
                "ledger": [
                    dict(r)
                    for r in db.execute("SELECT * FROM ledger ORDER BY entry_id")
                ],
                "actions": [
                    dict(r)
                    for r in db.execute("SELECT * FROM actions ORDER BY action_id")
                ],
                "extraction_leases": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM extraction_leases ORDER BY source_id"
                    )
                ],
                "extraction_attempts": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM extraction_attempts ORDER BY started_at,run_id"
                    )
                ],
            }
