"""Private per-process source, model, ownership and actual publication evidence."""

from copy import deepcopy
from importlib.metadata import version
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from .schemas import canonical, sha
from .transport import aggregate_usage, now


def publish(path, data):
    staged = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf8",
            dir=path.parent,
            prefix=".receipt-",
            suffix=".tmp",
            delete=False,
        ) as file:
            staged = Path(file.name)
            json.dump(data, file, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged, path)
    finally:
        if staged:
            staged.unlink(missing_ok=True)


class Receipt:
    def __init__(self, store, source_id, model, output_root=None, writer=publish):
        root = (Path(output_root) if output_root else Path.cwd() / ".runs").resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_id = str(uuid4())
        directory = root / run_id
        directory.mkdir(mode=0o700)
        self.path = directory / "receipt.json"
        self.store = store
        self.writer = writer
        self.recording_error = None
        before = store.snapshot(include_bodies=False)
        self.data = {
            "schema_version": 1,
            "run_id": run_id,
            "receipt_path": str(self.path),
            "status": "running",
            "execution_kind": "not_started",
            "started_at": now(),
            "finished_at": None,
            "database_path": store.path,
            "source_id": source_id,
            "requested_model": model,
            "versions": {
                name: version(name)
                for name in ("google-adk", "google-genai", "httpx", "pydantic")
            },
            "initial_state": before,
            "initial_state_sha256": sha(canonical(before).encode()),
            "dataset": before["dataset"],
            "fixture_sha256": before["fixture_sha256"],
            "source": None,
            "instructions_sha256": None,
            "claim": None,
            "model_observation": None,
            "reported_usage": aggregate_usage([]),
            "publication_status": "not_attempted",
            "final_state": None,
            "final_state_sha256": None,
            "actual_invoice": None,
            "facts_recorded_by_run": False,
            "result": None,
            "error": None,
            "cleanup_errors": [],
        }

    def guard(self):
        if self.recording_error:
            raise self.recording_error

    def save(self):
        try:
            self.writer(self.path, self.data)
        except BaseException as error:
            self.recording_error = error
            raise

    def observe(self, value):
        self.guard()
        self.data["model_observation"] = deepcopy(value)
        self.data["reported_usage"] = deepcopy(value["reported_usage"])
        self.save()

    def finish(self, result, error):
        self.data.update(
            status="failed" if error is not None else "completed",
            finished_at=now(),
            result=result if error is None else None,
        )
        try:
            final = self.store.snapshot(include_bodies=False)
            invoice = next(
                (
                    row
                    for row in final["invoices"]
                    if row["source_id"] == self.data["source_id"]
                ),
                None,
            )
            self.data.update(
                final_state=final,
                final_state_sha256=sha(canonical(final).encode()),
                actual_invoice=invoice,
                facts_recorded_by_run=bool(
                    invoice and invoice["extraction_run_id"] == self.data["run_id"]
                ),
            )
        except BaseException as snapshot_error:
            self.data["snapshot_error"] = {
                "name": type(snapshot_error).__name__,
                "message": str(snapshot_error),
            }
            if error is None:
                raise
        if error is not None:
            causes = []
            current = error
            while current is not None and len(causes) < 6:
                causes.append(
                    {
                        "name": type(current).__name__,
                        "message": str(current),
                        "http_status": getattr(current, "code", None)
                        or getattr(current, "status_code", None),
                    }
                )
                current = current.__cause__
            self.data["error"] = {
                "name": type(error).__name__,
                "message": str(error),
                "causes": causes,
            }
        self.save()
