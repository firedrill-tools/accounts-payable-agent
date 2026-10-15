"""CLI for a synthetic accounts payable desk."""

import argparse
import asyncio
import json
from pathlib import Path

from .agent import ADKInvoiceExtractor
from .store import APStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Google ADK accounts payable example")
    parser.add_argument("--db", default="ap.db", help="SQLite state file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("source_id")
    ingest.add_argument("file", type=Path)
    process = commands.add_parser("process")
    process.add_argument("source_id")
    process.add_argument("--model", help="Gemini model enabled for this account")
    commands.add_parser("list")
    commands.add_parser("ledger")
    for name in ("show", "reconcile", "post", "history"):
        commands.add_parser(name).add_argument("invoice_id", type=int)
    for name in ("approve", "reject"):
        command = commands.add_parser(name)
        command.add_argument("invoice_id", type=int)
        command.add_argument("--actor", required=True)
    vendor = commands.add_parser("add-vendor")
    vendor.add_argument("vendor_id")
    vendor.add_argument("name")
    purchase_order = commands.add_parser("set-po")
    purchase_order.add_argument("po_number")
    purchase_order.add_argument("vendor_id")
    purchase_order.add_argument("currency")
    purchase_order.add_argument("total_minor", type=int)
    args = parser.parse_args()
    store = APStore(args.db)
    if args.command == "seed":
        store.seed_demo()
        result = {"seeded": True}
    elif args.command == "ingest":
        result = store.ingest(args.source_id, args.file.read_text())
    elif args.command == "process":
        facts = asyncio.run(ADKInvoiceExtractor(model=args.model)(store.source_text(args.source_id)))
        result = store.record_facts(args.source_id, facts)
    elif args.command == "list":
        result = store.list_invoices()
    elif args.command == "ledger":
        result = store.ledger_entries()
    elif args.command == "show":
        result = store.get_invoice(args.invoice_id)
    elif args.command == "reconcile":
        result = store.reconcile(args.invoice_id)
    elif args.command == "approve":
        result = store.approve(args.invoice_id, args.actor)
    elif args.command == "reject":
        result = store.reject(args.invoice_id, args.actor)
    elif args.command == "post":
        result = store.post(args.invoice_id)
    elif args.command == "history":
        result = store.actions(args.invoice_id)
    elif args.command == "add-vendor":
        result = store.add_vendor(args.vendor_id, args.name)
    else:
        result = store.set_po(args.po_number, args.vendor_id, args.currency, args.total_minor)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
