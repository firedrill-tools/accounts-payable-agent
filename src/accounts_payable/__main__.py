"""JSON CLI for synthetic invoice extraction and separate human ledger decisions."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import signal
import sys

from .datasets import initialize, list_cases, select
from .schemas import MAX_TEXT
from .service import process_invoice_async
from .store import APStore


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--db", type=Path, default=Path("ap.db"))
    commands = command.add_subparsers(dest="command", required=True)
    commands.add_parser("list-cases")
    create = commands.add_parser("init")
    create.add_argument("--case", required=True)
    commands.add_parser("seed")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("source_id")
    ingest.add_argument("file", type=Path)
    process = commands.add_parser("process")
    target = process.add_mutually_exclusive_group(required=True)
    target.add_argument("source_id", nargs="?")
    target.add_argument("--case")
    process.add_argument("--model")
    process.add_argument(
        "--base-url", default="https://generativelanguage.googleapis.com"
    )
    process.add_argument("--timeout", type=float, default=120)
    process.add_argument("--output-root", type=Path)
    for name in ("list", "ledger", "snapshot", "leases"):
        commands.add_parser(name)
    source = commands.add_parser("source")
    source.add_argument("source_id")
    for name in ("show", "reconcile", "post", "history"):
        commands.add_parser(name).add_argument("invoice_id", type=int)
    for name in ("approve", "reject"):
        decision = commands.add_parser(name)
        decision.add_argument("invoice_id", type=int)
        decision.add_argument("--actor", required=True)
        if name == "approve":
            decision.add_argument("--review-token", required=True)
    vendor = commands.add_parser("add-vendor")
    vendor.add_argument("vendor_id")
    vendor.add_argument("name")
    vendor.add_argument("--inactive", action="store_true")
    active = commands.add_parser("set-vendor-active")
    active.add_argument("vendor_id")
    active.add_argument("active", choices=("true", "false"))
    po = commands.add_parser("set-po")
    for field in ("po_number", "vendor_id", "currency"):
        po.add_argument(field)
    po.add_argument("total_minor", type=int)
    po.add_argument("--inactive", action="store_true")
    recovery = commands.add_parser(
        "release-owner", help="release only an inspected, stopped extraction owner"
    )
    recovery.add_argument("source_id")
    recovery.add_argument("--token", required=True)
    recovery.add_argument("--owner-stopped", action="store_true", required=True)
    return command


async def run_process(store, args, source_id):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    registered = []
    interrupted = False

    def cancel():
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            task.cancel()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, cancel)
                registered.append(sig)
            except (NotImplementedError, RuntimeError):
                pass
        return await process_invoice_async(
            store,
            source_id,
            args.model,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
            output_root=args.output_root,
        )
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)


def execute(args):
    if args.command == "list-cases":
        return list_cases()
    if args.command == "init":
        return initialize(args.case, args.db)
    if args.command != "seed" and not args.db.is_file():
        raise ValueError(
            "database does not exist; initialize a private case or seed first"
        )
    store = APStore(args.db)
    if args.command == "seed":
        store.seed_demo()
        return {"seeded": True, "database_path": store.path}
    if args.command == "ingest":
        with args.file.open("rb") as source:
            body = source.read(MAX_TEXT * 4 + 1)
        if len(body) > MAX_TEXT * 4:
            raise ValueError("invoice source exceeds UTF-8 input limit")
        return store.ingest(args.source_id, body.decode("utf-8"))
    if args.command == "process":
        source_id = args.source_id
        if args.case:
            case, _, _ = select(args.case)
            state = store.snapshot(include_bodies=False)
            expected = {
                "id": "synthetic-payables-cases",
                "version": 1,
                "profile": case["profile"],
                "case_id": case["id"],
            }
            if (
                state["dataset"] != expected
                or state["fixture_sha256"] != case["sha256"]
            ):
                raise ValueError("database identity differs from the selected case")
            source_id = case["source_id"]
        return asyncio.run(run_process(store, args, source_id))
    if args.command == "list":
        return store.list_invoices()
    if args.command == "ledger":
        return store.ledger_entries()
    if args.command == "snapshot":
        return store.snapshot()
    if args.command == "source":
        return store.source_snapshot(args.source_id)
    if args.command == "leases":
        state = store.snapshot(include_bodies=False)
        return {key: state[key] for key in ("extraction_leases", "extraction_attempts")}
    if args.command == "release-owner":
        released = store.release_extraction(
            args.source_id,
            args.token,
            "Operator asserted owner stopped; inspect original receipt before retry",
        )
        if not released:
            raise ValueError(
                "source has no owner matching this token; no ownership was changed"
            )
        return {"source_id": args.source_id, "released": True}
    if args.command == "show":
        return store.review(args.invoice_id)
    if args.command == "reconcile":
        return store.reconcile(args.invoice_id)
    if args.command == "approve":
        return store.approve(
            args.invoice_id, args.actor, expected_review=args.review_token
        )
    if args.command == "reject":
        return store.reject(args.invoice_id, args.actor)
    if args.command == "post":
        return store.post(args.invoice_id)
    if args.command == "history":
        return store.actions(args.invoice_id)
    if args.command == "add-vendor":
        return store.add_vendor(args.vendor_id, args.name, not args.inactive)
    if args.command == "set-vendor-active":
        return store.set_vendor_active(args.vendor_id, args.active == "true")
    return store.set_po(
        args.po_number,
        args.vendor_id,
        args.currency,
        args.total_minor,
        not args.inactive,
    )


def main():
    args = parser().parse_args()
    # Keep this CLI's stdout/stderr machine readable; no process-global change in library APIs.
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(execute(args), indent=2, allow_nan=False))
        return 0
    except (Exception, asyncio.CancelledError, KeyboardInterrupt) as error:
        print(
            json.dumps(
                {
                    "error": type(error).__name__,
                    "message": str(error) or "Processing cancelled",
                    "receipt_path": getattr(error, "receipt_path", None),
                }
            ),
            file=sys.stderr,
        )
        return (
            130 if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
