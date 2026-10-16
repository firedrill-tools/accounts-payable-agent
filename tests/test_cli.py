"""Real CLI subprocesses and native ADK/Google HTTP over loopback."""

from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
from threading import Event, Thread

import pytest
from accounts_payable.datasets import initialize
from accounts_payable.store import APStore
from test_native import BODY, USAGE


def environment(keys=True):
    env = {
        **os.environ,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_ENTERPRISE",
    ):
        env.pop(name, None)
    if keys:
        env.update(GOOGLE_API_KEY="synthetic-cli-key", GEMINI_MODEL="gemini-2.5-flash")
    return env


def command(database, *args):
    return [sys.executable, "-m", "accounts_payable", "--db", str(database), *args]


@contextmanager
def server(value):
    requests = Queue()
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.put(
                json.loads(self.rfile.read(int(self.headers["content-length"])))
            )
            if value == "pending":
                release.wait(25)
            status = 503 if value == 503 else 200
            reply = (
                {
                    "error": {"code": 503, "message": "Synthetic unavailable"},
                    "usageMetadata": USAGE,
                }
                if status == 503
                else BODY
                if value == "pending"
                else value
            )
            raw = json.dumps(reply).encode()
            try:
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.send_header("x-request-id", "synthetic-cli-request")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.daemon_threads = True
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", requests
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(3)


def run(database, *args, keys=True, cwd=None):
    return subprocess.run(
        command(database, *args),
        cwd=cwd or database.parent,
        env=environment(keys),
        capture_output=True,
        text=True,
        timeout=25,
    )


def test_real_cli_native_extract_then_separate_named_review_and_post(tmp_path):
    database = tmp_path / "ap.db"
    initialize("starter", database)
    with server(BODY) as (endpoint, requests):
        child = run(database, "process", "--case", "starter", "--base-url", endpoint)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    invoice = result["invoice"]
    assert invoice["status"] == "pending_approval" and requests.qsize() == 1
    store = APStore(database)
    assert store.ledger_entries() == [] and store.actions(invoice["invoice_id"]) == []
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    assert (
        receipt["model_observation"]["provider_observations"][0]["request_id"]
        == "synthetic-cli-request"
    )
    assert "synthetic-cli-key" not in json.dumps(receipt)
    assert run(database, "approve", "1", "--actor", "reviewer").returncode == 2
    review = json.loads(run(database, "show", "1", keys=False).stdout)
    approved = run(
        database,
        "approve",
        "1",
        "--actor",
        "reviewer",
        "--review-token",
        review["review_fingerprint"],
        keys=False,
    )
    assert json.loads(approved.stdout)["status"] == "approved"
    posted = run(database, "post", "1", keys=False)
    assert json.loads(posted.stdout)["posting_replayed"] is False
    replay = run(database, "post", "1", keys=False)
    assert json.loads(replay.stdout)["posting_replayed"] is True
    cached = run(database, "process", "--case", "starter", keys=False)
    assert json.loads(cached.stdout)["execution_kind"] == "model_free_existing_invoice"
    assert len(store.ledger_entries()) == 1 and len(store.actions(1)) == 2


@pytest.mark.parametrize("kind", ["unfinished", "503"])
def test_real_cli_native_failures_keep_raw_failed_usage_without_facts(tmp_path, kind):
    database = tmp_path / "ap.db"
    initialize("starter", database)
    body = deepcopy(BODY)
    body["candidates"][0]["finishReason"] = "MAX_TOKENS"
    with server(503 if kind == "503" else body) as (endpoint, requests):
        child = run(database, "process", "--case", "starter", "--base-url", endpoint)
    assert child.returncode == 1 and child.stdout == ""
    receipt = json.loads(Path(json.loads(child.stderr)["receipt_path"]).read_text())
    assert (
        receipt["status"] == "failed"
        and receipt["result"] is None
        and receipt["reported_usage"]["fields"] == USAGE
    )
    assert receipt["final_state"]["invoices"] == [] and requests.qsize() == 1
    assert receipt["model_observation"]["lifecycle"]["http_closed"] is True


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_real_cli_signal_cleans_session_client_owner_and_prevents_late_facts(
    tmp_path, sig
):
    database = tmp_path / "ap.db"
    initialize("starter", database)
    with server("pending") as (endpoint, requests):
        child = subprocess.Popen(
            command(database, "process", "--case", "starter", "--base-url", endpoint),
            cwd=tmp_path,
            env=environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            requests.get(timeout=20)
            child.send_signal(sig)
            out, err = child.communicate(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
    assert child.returncode == 130 and out == "", err
    receipt = json.loads(Path(json.loads(err)["receipt_path"]).read_text())
    assert receipt["status"] == "failed" and receipt["result"] is None
    assert all(receipt["model_observation"]["lifecycle"].values())
    assert (
        receipt["final_state"]["invoices"] == []
        and receipt["final_state"]["extraction_leases"] == []
    )
    assert APStore(database).list_invoices() == []


def test_real_cli_key_free_private_init_identity_guard_and_stopped_owner_recovery(
    tmp_path,
):
    database = tmp_path / "busy.db"
    listing = run(database, "list-cases", keys=False)
    assert (
        listing.returncode == 0
        and len(json.loads(listing.stdout)["cases"]) == 12
        and not database.exists()
    )
    assert run(database, "init", "--case", "busy", keys=False).returncode == 0
    assert database.stat().st_mode & 0o777 == 0o600
    assert run(database, "init", "--case", "busy", keys=False).returncode == 1
    assert run(database, "process", "--case", "starter", keys=False).returncode == 1
    failed = run(database, "process", "--case", "busy", keys=False)
    assert failed.returncode == 1
    receipt = json.loads(Path(json.loads(failed.stderr)["receipt_path"]).read_text())
    assert (
        receipt["reported_usage"]["prepared_attempts"] == 0
        and len(receipt["final_state"]["ledger"]) == 6
    )
    store = APStore(database)
    claim = store.claim_extraction(
        "mail-b013",
        "stopped-test-owner",
        str(tmp_path / "original-receipt"),
        "model_extraction",
    )
    assert (
        run(
            database,
            "release-owner",
            "mail-b013",
            "--token",
            claim["token"],
            keys=False,
        ).returncode
        == 2
    )
    assert (
        run(
            database,
            "release-owner",
            "mail-b013",
            "--token",
            "wrong",
            "--owner-stopped",
            keys=False,
        ).returncode
        == 1
    )
    assert len(store.snapshot()["extraction_leases"]) == 1
    assert (
        run(
            database,
            "release-owner",
            "mail-b013",
            "--token",
            claim["token"],
            "--owner-stopped",
            keys=False,
        ).returncode
        == 0
    )
    assert store.snapshot()["extraction_leases"] == []
