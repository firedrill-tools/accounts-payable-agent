"""Fresh locked wheel and source installs, exercised outside the checkout."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import tomllib
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
UV = os.environ.get("UV") or shutil.which("uv")
if not UV:
    raise RuntimeError("uv is required for distribution verification")
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
wheel = ROOT / "dist" / f"accounts_payable_agent-{VERSION}-py3-none-any.whl"
sdist = ROOT / "dist" / f"accounts_payable_agent-{VERSION}.tar.gz"
env = {**os.environ}
for key in (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
):
    env.pop(key, None)


def run(command, cwd, check=True):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=check,
        timeout=180,
    )


def exercise(python, base):
    cli = [str(python), "-I", "-m", "accounts_payable"]
    run([str(python.parent / "payables"), "--help"], base)
    manifest = json.loads(run([*cli, "list-cases"], base).stdout)
    assert len(manifest["cases"]) == 12 and manifest["version"] == 1
    for case in manifest["cases"]:
        path = base / f"{case['id']}.db"
        result = json.loads(
            run([*cli, "--db", str(path), "init", "--case", case["id"]], base).stdout
        )
        assert (
            result["fixture_sha256"] == case["sha256"]
            and path.stat().st_mode & 0o777 == 0o600
        )
    failed = run(
        [*cli, "--db", str(base / "starter.db"), "process", "--case", "starter"],
        base,
        False,
    )
    assert failed.returncode == 1
    receipt = json.loads(Path(json.loads(failed.stderr)["receipt_path"]).read_text())
    assert (
        receipt["status"] == "failed"
        and receipt["result"] is None
        and receipt["reported_usage"]["prepared_attempts"] == 0
    )
    cached = json.loads(
        run(
            [
                *cli,
                "--db",
                str(base / "already-posted.db"),
                "process",
                "--case",
                "already-posted",
            ],
            base,
        ).stdout
    )
    assert (
        cached["execution_kind"] == "model_free_existing_invoice"
        and cached["reported_usage"]["prepared_attempts"] == 0
    )
    program = """
import json
from pathlib import Path
import sys
import accounts_payable
from accounts_payable.schemas import ExtractedInvoice, InvoiceFacts, parse_amount, to_facts
from accounts_payable.store import APStore
assert Path(accounts_payable.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
base = Path(sys.argv[1]); busy = APStore(base/'busy.db').snapshot()
assert len(busy['vendors']) == 8 and len(busy['purchase_orders']) == 20 and len(busy['inbox']) == 18
assert len(busy['invoices']) == 12 and len(busy['ledger']) == 6 and len(busy['actions']) == 15
assert all(row['facts_origin'] == 'human_import' for row in busy['invoices']) and busy['extraction_attempts'] == []
store = APStore(base/'starter.db'); text = store.source_text('mail-1048')
facts = to_facts(ExtractedInvoice(vendor_name='Northstar Paper Supply', invoice_number='INV-1048', po_number='PO-204', currency='USD', total_text='125.00', due_date='2026-11-13'), text)
assert facts.total_minor == 12500 and not facts.concerns
invoice = store.record_facts('mail-1048', facts); token = store.review(invoice['invoice_id'])['review_fingerprint']
store.approve(invoice['invoice_id'], 'installed-human-reviewer', expected_review=token)
assert store.post(invoice['invoice_id'])['posting_replayed'] is False
assert store.post(invoice['invoice_id'])['posting_replayed'] is True
assert len(store.ledger_entries()) == 1 and len(store.actions(invoice['invoice_id'])) == 2
store.ingest('another-source', 'Synthetic human import of a distinct full-order invoice.')
other = store.record_facts('another-source', InvoiceFacts(**{**facts.model_dump(), 'invoice_number': 'INV-1049'}))
assert other['status'] == 'needs_review' and 'purchase order remaining balance exceeded' in other['issues']
assert parse_amount('12.345', 'KWD') == 12345 and parse_amount('90071992547409.91', 'CAD') == 9007199254740991
try: parse_amount('125.50', 'JPY')
except ValueError: pass
else: raise AssertionError('fractional JPY accepted')
"""
    run([str(python), "-I", "-c", program, str(base)], base)


with TemporaryDirectory(prefix="payables-distribution-") as directory:
    temp = Path(directory)
    with ZipFile(wheel) as archive:
        assert "accounts_payable/sample_data/manifest.json" in archive.namelist()
        assert (
            len(
                [
                    name
                    for name in archive.namelist()
                    if name.startswith("accounts_payable/sample_data/")
                    and name.endswith(".json")
                ]
            )
            == 13
        )
    requirements = temp / "requirements.txt"
    requirements.write_text(
        run(
            [
                UV,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
            ],
            ROOT,
        ).stdout
    )
    wheel_env = temp / "wheel-env"
    run([UV, "venv", "--python", sys.executable, str(wheel_env)], temp)
    python = wheel_env / "bin/python"
    run([UV, "pip", "sync", "--python", str(python), str(requirements)], temp)
    run([UV, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], temp)
    wheel_case = temp / "wheel-case"
    wheel_case.mkdir()
    exercise(python, wheel_case)
    extracted = temp / "source"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    (source,) = extracted.iterdir()
    assert (source / "uv.lock").exists()
    run(
        [UV, "sync", "--locked", "--no-dev", "--no-editable", "--project", str(source)],
        temp,
    )
    assert (source/'firedrill/scenario.json').exists() and not (source/'firedrill/node_modules').exists()
    run(['npm','ci','--prefix','firedrill','--ignore-scripts','--no-audit','--no-fund'],source)
    run(['npm','test','--prefix','firedrill'],source)
    source_case = temp / "source-case"
    source_case.mkdir()
    exercise(source / ".venv/bin/python", source_case)
print(
    "Fresh locked wheel/sdist verified outside checkout: console/module CLI, twelve private cases, failed key-preflight receipt, busy human-import history, source-bound exact money, review/approval/post and exact recovery. No live provider or financial transfer claim."
)
