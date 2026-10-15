"""SQLite invoice inbox, vendor/PO directory, approval log and synthetic ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

from .agent import InvoiceFacts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name_key(value: str) -> str:
    return " ".join(value.casefold().split())


class APStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inbox (
                    source_id TEXT PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vendors (
                    vendor_id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL CHECK (active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS purchase_orders (
                    po_number TEXT PRIMARY KEY, vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
                    currency TEXT NOT NULL, total_minor INTEGER NOT NULL CHECK (total_minor > 0),
                    active INTEGER NOT NULL CHECK (active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL UNIQUE REFERENCES inbox(source_id),
                    vendor_name TEXT, vendor_id TEXT REFERENCES vendors(vendor_id),
                    invoice_number TEXT, po_number TEXT, currency TEXT,
                    total_minor INTEGER, due_date TEXT, concerns_json TEXT NOT NULL,
                    issues_json TEXT NOT NULL, status TEXT NOT NULL,
                    approval_actor TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(invoice_id),
                    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
                    invoice_number TEXT NOT NULL, currency TEXT NOT NULL,
                    total_minor INTEGER NOT NULL, posted_at TEXT NOT NULL,
                    UNIQUE(vendor_id, invoice_number)
                );
                CREATE TABLE IF NOT EXISTS actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id),
                    action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_vendor(self, vendor_id: str, name: str, active: bool = True) -> dict:
        if not vendor_id.strip() or not name.strip():
            raise ValueError("vendor ID and name are required")
        with self._db() as db:
            db.execute("INSERT INTO vendors VALUES (?,?,?,?)", (
                vendor_id.strip(), name.strip(), _name_key(name), int(active)
            ))
            return dict(db.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id.strip(),)).fetchone())

    def set_po(self, po_number: str, vendor_id: str, currency: str, total_minor: int,
               active: bool = True) -> dict:
        if not po_number.strip() or total_minor <= 0 or len(currency) != 3:
            raise ValueError("PO number, positive minor-unit total and ISO currency are required")
        with self._db() as db:
            db.execute("""
                INSERT INTO purchase_orders VALUES (?,?,?,?,?)
                ON CONFLICT(po_number) DO UPDATE SET vendor_id=excluded.vendor_id,
                currency=excluded.currency, total_minor=excluded.total_minor, active=excluded.active
            """, (po_number.strip(), vendor_id, currency.upper(), total_minor, int(active)))
            return dict(db.execute("SELECT * FROM purchase_orders WHERE po_number=?", (po_number.strip(),)).fetchone())

    def seed_demo(self) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO vendors VALUES (?,?,?,1)",
                       ("vendor-northstar", "Northstar Paper Supply", "northstar paper supply"))
            db.execute("INSERT OR IGNORE INTO vendors VALUES (?,?,?,1)",
                       ("vendor-orbit", "Orbit Facilities", "orbit facilities"))
            db.execute("INSERT OR IGNORE INTO purchase_orders VALUES (?,?,?,?,1)",
                       ("PO-204", "vendor-northstar", "USD", 12500))
            db.execute("INSERT OR IGNORE INTO purchase_orders VALUES (?,?,?,?,1)",
                       ("PO-208", "vendor-orbit", "USD", 85000))

    def ingest(self, source_id: str, body: str) -> dict:
        if not source_id.strip() or not body.strip():
            raise ValueError("source ID and invoice text are required")
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM inbox WHERE source_id=?", (source_id,)).fetchone()
            if prior:
                if prior["sha256"] != digest:
                    raise ValueError("source ID reused for different invoice content")
                return {"source_id": source_id, "sha256": digest, "replayed": True}
            db.execute("INSERT INTO inbox VALUES (?,?,?)", (source_id, body, digest))
            return {"source_id": source_id, "sha256": digest, "replayed": False}

    def source_text(self, source_id: str) -> str:
        with self._db() as db:
            row = db.execute("SELECT body FROM inbox WHERE source_id=?", (source_id,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            return row["body"]

    def _assess(self, db: sqlite3.Connection, invoice: dict) -> tuple[str, list[str], str | None]:
        issues = list(invoice["concerns"])
        vendor_id = None
        if not invoice["vendor_name"]:
            issues.append("missing vendor")
        else:
            vendor = db.execute("SELECT * FROM vendors WHERE name_key=?",
                                (_name_key(invoice["vendor_name"]),)).fetchone()
            if vendor is None:
                issues.append("unknown vendor")
            elif not vendor["active"]:
                issues.append("inactive vendor")
            else:
                vendor_id = vendor["vendor_id"]

        if not invoice["invoice_number"]:
            issues.append("missing invoice number")
        if not invoice["currency"] or len(invoice["currency"]) != 3:
            issues.append("missing or invalid currency")
        if invoice["total_minor"] is None or invoice["total_minor"] <= 0:
            issues.append("missing or nonpositive total")
        if not invoice["po_number"]:
            issues.append("missing purchase order")
        else:
            po = db.execute("SELECT * FROM purchase_orders WHERE po_number=?",
                            (invoice["po_number"],)).fetchone()
            if po is None:
                issues.append("unknown purchase order")
            elif not po["active"]:
                issues.append("inactive purchase order")
            else:
                if vendor_id and po["vendor_id"] != vendor_id:
                    issues.append("purchase order vendor mismatch")
                if invoice["currency"] and po["currency"] != invoice["currency"].upper():
                    issues.append("purchase order currency mismatch")
                if invoice["total_minor"] is not None and po["total_minor"] != invoice["total_minor"]:
                    issues.append("purchase order amount mismatch")

        if vendor_id and invoice["invoice_number"]:
            duplicate = db.execute("""
                SELECT invoice_id FROM invoices WHERE vendor_id=? AND invoice_number=?
                AND invoice_id != ? AND status != 'duplicate' LIMIT 1
            """, (vendor_id, invoice["invoice_number"], invoice.get("invoice_id") or -1)).fetchone()
            if duplicate:
                issues.append(f"duplicate of invoice {duplicate['invoice_id']}")
                return "duplicate", issues, vendor_id
        return ("needs_review" if issues else "pending_approval"), issues, vendor_id

    @staticmethod
    def _invoice(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["concerns"] = json.loads(result.pop("concerns_json"))
        result["issues"] = json.loads(result.pop("issues_json"))
        return result

    def record_facts(self, source_id: str, facts: InvoiceFacts) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM inbox WHERE source_id=?", (source_id,)).fetchone():
                raise KeyError(source_id)
            prior = db.execute("SELECT * FROM invoices WHERE source_id=?", (source_id,)).fetchone()
            if prior:
                return self._invoice(prior)
            invoice = facts.model_dump()
            for field in ("vendor_name", "invoice_number", "po_number", "currency", "due_date"):
                if invoice[field] is not None:
                    invoice[field] = invoice[field].strip() or None
            if invoice["currency"]:
                invoice["currency"] = invoice["currency"].upper()
            status, issues, vendor_id = self._assess(db, invoice)
            cursor = db.execute("""
                INSERT INTO invoices (source_id,vendor_name,vendor_id,invoice_number,po_number,
                    currency,total_minor,due_date,concerns_json,issues_json,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (source_id, invoice["vendor_name"], vendor_id, invoice["invoice_number"],
                  invoice["po_number"], invoice["currency"], invoice["total_minor"],
                  invoice["due_date"], json.dumps(invoice["concerns"]), json.dumps(issues),
                  status, _now()))
            return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                            (cursor.lastrowid,)).fetchone())

    def _checked(self, db: sqlite3.Connection, invoice_id: int, allowed: tuple[str, ...]) -> dict:
        row = db.execute("SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone()
        if row is None:
            raise KeyError(invoice_id)
        invoice = self._invoice(row)
        if invoice["status"] not in allowed:
            raise ValueError(f"invoice is {invoice['status']}; expected {', '.join(allowed)}")
        return invoice

    def _refresh(self, db: sqlite3.Connection, invoice: dict) -> dict:
        status, issues, vendor_id = self._assess(db, invoice)
        db.execute("UPDATE invoices SET status=?, issues_json=?, vendor_id=? WHERE invoice_id=?",
                   (status, json.dumps(issues), vendor_id, invoice["invoice_id"]))
        return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                        (invoice["invoice_id"],)).fetchone())

    def reconcile(self, invoice_id: int) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._refresh(db, self._checked(db, invoice_id,
                                                  ("needs_review", "pending_approval")))

    def approve(self, invoice_id: int, actor: str) -> dict:
        if not actor.strip():
            raise ValueError("approval actor is required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._checked(db, invoice_id, ("pending_approval",))
            invoice = self._refresh(db, invoice)
            if invoice["status"] != "pending_approval":
                return invoice
            db.execute("UPDATE invoices SET status='approved', approval_actor=? WHERE invoice_id=?",
                       (actor.strip(), invoice_id))
            db.execute("INSERT INTO actions (invoice_id,action,actor,at) VALUES (?,?,?,?)",
                       (invoice_id, "approved", actor.strip(), _now()))
            return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                            (invoice_id,)).fetchone())

    def reject(self, invoice_id: int, actor: str) -> dict:
        if not actor.strip():
            raise ValueError("decision actor is required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._checked(db, invoice_id, ("pending_approval",))
            db.execute("UPDATE invoices SET status='rejected', approval_actor=? WHERE invoice_id=?",
                       (actor.strip(), invoice_id))
            db.execute("INSERT INTO actions (invoice_id,action,actor,at) VALUES (?,?,?,?)",
                       (invoice_id, "rejected", actor.strip(), _now()))
            return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                            (invoice_id,)).fetchone())

    def post(self, invoice_id: int) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._checked(db, invoice_id, ("approved",))
            status, issues, vendor_id = self._assess(db, invoice)
            if status != "pending_approval":
                db.execute("UPDATE invoices SET status='needs_review', issues_json=?, approval_actor=NULL WHERE invoice_id=?",
                           (json.dumps(issues), invoice_id))
                return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                                (invoice_id,)).fetchone())
            db.execute("""
                INSERT INTO ledger (invoice_id,vendor_id,invoice_number,currency,total_minor,posted_at)
                VALUES (?,?,?,?,?,?)
            """, (invoice_id, vendor_id, invoice["invoice_number"], invoice["currency"],
                  invoice["total_minor"], _now()))
            db.execute("UPDATE invoices SET status='posted' WHERE invoice_id=?", (invoice_id,))
            db.execute("INSERT INTO actions (invoice_id,action,actor,at) VALUES (?,?,?,?)",
                       (invoice_id, "posted", invoice["approval_actor"], _now()))
            return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                            (invoice_id,)).fetchone())

    def get_invoice(self, invoice_id: int) -> dict | None:
        with self._db() as db:
            return self._invoice(db.execute("SELECT * FROM invoices WHERE invoice_id=?",
                                            (invoice_id,)).fetchone())

    def list_invoices(self) -> list[dict]:
        with self._db() as db:
            return [self._invoice(row) for row in db.execute(
                "SELECT * FROM invoices ORDER BY invoice_id")]

    def ledger_entries(self) -> list[dict]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM ledger ORDER BY entry_id")]

    def actions(self, invoice_id: int) -> list[dict]:
        with self._db() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM actions WHERE invoice_id=? ORDER BY action_id", (invoice_id,))]
