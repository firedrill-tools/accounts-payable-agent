"""Coherent human-import business fixtures; manifest criteria never enter model input."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .schemas import (
    InvoiceFacts,
    SAFE_INTEGER,
    bounded_text,
    currency,
    identifier,
    identity,
    sha,
)
from .store import APStore

MAX_FIXTURE = 4 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Vendor(StrictModel):
    vendor_id: str
    name: str = Field(min_length=1, max_length=200)
    active: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def valid(self):
        identifier(self.vendor_id, "vendor ID")
        return self


class PurchaseOrder(StrictModel):
    po_number: str
    vendor_id: str
    currency: str
    total_minor: int = Field(ge=1, le=SAFE_INTEGER, strict=True)
    active: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def valid(self):
        identifier(self.po_number, "PO number")
        identifier(self.vendor_id, "vendor ID")
        currency(self.currency)
        return self


class Source(StrictModel):
    source_id: str
    body: str

    @model_validator(mode="after")
    def valid(self):
        identifier(self.source_id, "source ID")
        bounded_text(self.body)
        return self


class HumanInvoice(StrictModel):
    source_id: str
    facts: InvoiceFacts
    status: Literal[
        "pending_approval",
        "needs_review",
        "duplicate",
        "approved",
        "posted",
        "rejected",
    ]
    actor: str | None = None
    at: str
    origin: Literal["human_import"] = "human_import"

    @model_validator(mode="after")
    def valid(self):
        identifier(self.source_id, "source ID")
        timestamp = datetime.fromisoformat(self.at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("human history timestamp must be UTC")
        if self.status in {"approved", "posted", "rejected"}:
            identifier(self.actor, "human operator")
        return self


class Identity(StrictModel):
    id: Literal["synthetic-payables-cases"]
    version: Literal[1]
    profile: Literal["starter", "busy", "ambiguous", "boundary"]
    case_id: str

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported fixture version")
        return value


class Fixture(StrictModel):
    synthetic: Literal[True]
    dataset: Identity
    vendors: tuple[Vendor, ...] = Field(min_length=1, max_length=100)
    purchase_orders: tuple[PurchaseOrder, ...] = Field(max_length=200)
    sources: tuple[Source, ...] = Field(min_length=1, max_length=100)
    prior_invoices: tuple[HumanInvoice, ...] = Field(default=(), max_length=100)

    @field_validator("synthetic", mode="before")
    @classmethod
    def synthetic_only(cls, value):
        if value is not True:
            raise ValueError("fixture must be synthetic")
        return value

    @model_validator(mode="after")
    def coherent(self):
        vendors = {item.vendor_id for item in self.vendors}
        sources = {item.source_id for item in self.sources}
        if len(vendors) != len(self.vendors) or len(sources) != len(self.sources):
            raise ValueError("duplicate vendor/source identity")
        if len({p.po_number for p in self.purchase_orders}) != len(
            self.purchase_orders
        ):
            raise ValueError("duplicate PO identity")
        if any(p.vendor_id not in vendors for p in self.purchase_orders):
            raise ValueError("PO has unknown directory vendor")
        if len({h.source_id for h in self.prior_invoices}) != len(
            self.prior_invoices
        ) or any(h.source_id not in sources for h in self.prior_invoices):
            raise ValueError("invalid prior source relationship")
        return self


def bounded_read(path):
    with Path(path).open("rb") as file:
        raw = file.read(MAX_FIXTURE + 1)
    if len(raw) > MAX_FIXTURE:
        raise ValueError("payables fixture exceeds 4 MiB")
    return raw


def load_fixture(path):
    raw = bounded_read(path)
    return Fixture.model_validate_json(raw), sha(raw)


def data_root():
    bundled = Path(__file__).parent / "sample_data"
    return bundled if bundled.exists() else Path(__file__).parents[2] / "data"


def load_manifest(root=None):
    root = Path(root) if root is not None else data_root()
    raw = bounded_read(root / "manifest.json")
    manifest = json.loads(raw)
    if (
        manifest.get("id") != "synthetic-payables-cases"
        or type(manifest.get("version")) is not int
        or manifest["version"] != 1
        or manifest.get("synthetic") is not True
    ):
        raise ValueError("unsupported manifest identity")
    if not isinstance(manifest.get("cases"), list) or not manifest["cases"]:
        raise ValueError("manifest has no cases")
    ids = set()
    files = set()
    for case in manifest["cases"]:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not case["id"].strip()
            or case["id"] in ids
        ):
            raise ValueError("invalid/duplicate case identity")
        ids.add(case["id"])
        file = case.get("file")
        if (
            not isinstance(file, str)
            or not file
            or Path(file).is_absolute()
            or ".." in Path(file).parts
            or file in files
        ):
            raise ValueError("invalid case path")
        files.add(file)
        target = (root / file).resolve(strict=True)
        if not target.is_relative_to(root.resolve(strict=True)):
            raise ValueError("case path escapes dataset")
        fixture, digest = load_fixture(target)
        if digest != case.get("sha256") or fixture.dataset.model_dump() != {
            "id": manifest["id"],
            "version": 1,
            "profile": case.get("profile"),
            "case_id": case["id"],
        }:
            raise ValueError("case hash/identity differs")
        if case.get("source_id") not in {
            source.source_id for source in fixture.sources
        }:
            raise ValueError("selected source is unknown")
        for key, actual in [
            ("vendors", len(fixture.vendors)),
            ("purchase_orders", len(fixture.purchase_orders)),
            ("sources", len(fixture.sources)),
            ("prior_invoices", len(fixture.prior_invoices)),
        ]:
            if type(case.get(key)) is not int or case[key] != actual:
                raise ValueError("case population differs")
    return manifest, sha(raw), root


def list_cases(root=None):
    manifest, digest, _ = load_manifest(root)
    return {**manifest, "manifest_sha256": digest}


def select(case_id, root=None):
    manifest, digest, root = load_manifest(root)
    case = next((item for item in manifest["cases"] if item["id"] == case_id), None)
    if case is None:
        raise ValueError("unknown case")
    path = (root / case["file"]).resolve(strict=True)
    fixture, file_digest = load_fixture(path)
    if file_digest != case["sha256"]:
        raise ValueError("case changed before selection")
    return case, path, digest


def initialize_fixture(fixture_path, path):
    fixture, digest = load_fixture(fixture_path)
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    try:
        store = APStore(path)
        for vendor in fixture.vendors:
            store.add_vendor(vendor.vendor_id, vendor.name, vendor.active)
        for po in fixture.purchase_orders:
            store.set_po(
                po.po_number, po.vendor_id, po.currency, po.total_minor, po.active
            )
        for source in fixture.sources:
            store.ingest(source.source_id, source.body)
        for imported in fixture.prior_invoices:
            invoice = store.record_facts(
                imported.source_id, imported.facts, origin="human_import"
            )
            invoice_id = invoice["invoice_id"]
            if imported.status in {"approved", "posted"}:
                store.approve(
                    invoice_id,
                    imported.actor,
                    expected_review=store.review(invoice_id)["review_fingerprint"],
                    origin="human_import",
                )
                if imported.status == "posted":
                    store.post(invoice_id, origin="human_import")
            elif imported.status == "rejected":
                store.reject(invoice_id, imported.actor, origin="human_import")
            if store.get_invoice(invoice_id)["status"] != imported.status:
                raise ValueError(
                    "imported business state does not match its declared status"
                )
            with store._db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE invoices SET created_at=? WHERE invoice_id=?",
                    (imported.at, invoice_id),
                )
                db.execute(
                    "UPDATE actions SET at=? WHERE invoice_id=?",
                    (imported.at, invoice_id),
                )
                db.execute(
                    "UPDATE ledger SET posted_at=? WHERE invoice_id=?",
                    (imported.at, invoice_id),
                )
        if load_fixture(fixture_path)[1] != digest:
            raise ValueError("fixture changed during initialization")
        with store._db() as db:
            db.execute(
                "INSERT INTO dataset_metadata VALUES(1,?,?)",
                (json.dumps(fixture.dataset.model_dump()), digest),
            )
        return store
    except BaseException:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        raise


def initialize(case_id, path, root=None):
    case, source, digest = select(case_id, root)
    store = initialize_fixture(source, path)
    return {
        "database_path": store.path,
        "dataset": store.snapshot(include_bodies=False)["dataset"],
        "fixture_sha256": case["sha256"],
        "manifest_sha256": digest,
        "source_id": case["source_id"],
    }
