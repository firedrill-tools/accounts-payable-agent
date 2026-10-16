"""Owned native Gemini Developer API clients and pre-normalization evidence."""

from datetime import datetime, timezone
import json
import os
from urllib.parse import urlsplit
import httpx
from google import genai
from google.genai import types
from .schemas import sha

LIMIT = 1024 * 1024
FIELDS = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")


def now():
    return datetime.now(timezone.utc).isoformat()


def invalid_constant(value):
    raise ValueError("nonfinite JSON constant")


def reported_usage(body):
    raw = body.get("usageMetadata") if isinstance(body, dict) else None
    fields = {
        key: raw.get(key)
        if isinstance(raw, dict)
        and type(raw.get(key)) is int
        and 0 <= raw[key] <= 2**53 - 1
        else None
        for key in FIELDS
    }
    return {
        "fields": fields,
        "complete": all(value is not None for value in fields.values()),
        "cost_usd": None,
    }


def aggregate_usage(observations):
    values = [item.get("reported_usage") for item in observations]
    fields = {
        key: sum(value["fields"][key] for value in values)
        if values
        and all(value and value["fields"][key] is not None for value in values)
        else None
        for key in FIELDS
    }
    fields = {
        key: value if value is None or value <= 2**53 - 1 else None
        for key, value in fields.items()
    }
    return {
        "prepared_attempts": len(observations),
        "complete": bool(values)
        and all(value is not None for value in fields.values()),
        "fields": fields,
        "cost_usd": None,
    }


def validate_config(model, base_url):
    if (
        not isinstance(model, str)
        or not model.startswith("gemini-")
        or len(model) > 200
        or any(c.isspace() for c in model)
    ):
        raise ValueError("GEMINI_MODEL must name an enabled Gemini Developer API model")
    for key in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"):
        if os.getenv(key, "false").strip().lower() not in {"false", "0", ""}:
            raise ValueError(
                "explicit Developer API only; ambient Vertex/enterprise selection is unsupported"
            )
    url = urlsplit(base_url)
    if (
        not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not (
            url.scheme == "https"
            or url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise ValueError(
            "endpoint requires HTTPS or loopback HTTP without credentials/query/fragment"
        )


def validate_completion(body):
    if not isinstance(body, dict):
        raise ValueError("Gemini response is not a JSON object")
    feedback = body.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason") not in {
        None,
        "BLOCK_REASON_UNSPECIFIED",
    }:
        raise ValueError("Gemini prompt was blocked")
    candidates = body.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or not isinstance(candidates[0], dict)
    ):
        raise ValueError("exactly one Gemini candidate required")
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise ValueError(
            f"Gemini generation did not complete ({candidate.get('finishReason')!r})"
        )
    if any(
        isinstance(rating, dict) and rating.get("blocked") is True
        for rating in candidate.get("safetyRatings") or []
    ):
        raise ValueError("Gemini candidate was blocked")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if (
        not isinstance(content, dict)
        or content.get("role") not in {None, "model"}
        or not isinstance(parts, list)
        or not parts
        or any(not isinstance(part, dict) for part in parts)
    ):
        raise ValueError("Gemini candidate lacks usable model content")
    if any(
        any(
            part.get(key) is not None
            for key in (
                "functionCall",
                "executableCode",
                "codeExecutionResult",
                "inlineData",
                "fileData",
            )
        )
        for part in parts
    ):
        raise ValueError(
            "invoice extraction requires text without executable/tool/media content"
        )
    if not any(
        not part.get("thought")
        and isinstance(part.get("text"), str)
        and part["text"].strip()
        for part in parts
    ):
        raise ValueError("Gemini candidate has no final text")


class NativeEvidence:
    def __init__(self, publish, guard):
        self.observations = []
        self.publish = publish
        self.guard = guard
        self.failed = None

    def check(self):
        self.guard()
        if self.failed:
            raise RuntimeError("previous model request failed") from self.failed

    async def request(self, request):
        self.check()
        if request.method != "POST" or not request.url.path.endswith(
            ":generateContent"
        ):
            raise ValueError("only async JSON generateContent is qualified")
        if self.observations:
            raise ValueError("invoice model generation limit (1) reached")
        raw = await request.aread()
        if len(raw) > LIMIT:
            raise ValueError("provider request exceeds 1 MiB")
        body = json.loads(raw)
        self.observations.append(
            {
                "attempt": 1,
                "status": "prepared",
                "started_at": now(),
                "finished_at": None,
                "path": request.url.path,
                "input_sha256": sha(raw),
                "request_body": body,
                "http_status": None,
                "request_id": None,
                "response_id": None,
                "response_model": None,
                "finish_reason": None,
                "body": None,
                "reported_usage": None,
                "error": None,
            }
        )
        self.publish()
        self.check()

    async def response(self, response):
        item = self.observations[-1]
        item.update(
            status="received",
            http_status=response.status_code,
            request_id=response.headers.get("x-request-id")
            or response.headers.get("request-id"),
        )
        try:
            raw = await response.aread()
            captured = raw[:LIMIT]
            truncated = len(raw) > LIMIT
            try:
                body = json.loads(captured, parse_constant=invalid_constant)
            except ValueError:
                body = captured.decode("utf8", errors="replace")
            item.update(
                body=body,
                truncated=truncated,
                body_sha256=None if truncated else sha(raw),
                captured_sha256=sha(captured),
                reported_usage=reported_usage(body),
            )
            if isinstance(body, dict):
                candidates = body.get("candidates")
                candidate = (
                    candidates[0]
                    if isinstance(candidates, list)
                    and candidates
                    and isinstance(candidates[0], dict)
                    else {}
                )
                item.update(
                    response_id=body.get("responseId"),
                    response_model=body.get("modelVersion"),
                    finish_reason=candidate.get("finishReason"),
                )
            self.publish()
            self.check()
            if truncated:
                raise ValueError("provider response exceeds 1 MiB evidence limit")
            if response.is_success:
                validate_completion(body)
                item["status"] = "accepted"
            else:
                item["status"] = "http_error"
            self.publish()
        except BaseException as error:
            self.failed = error
            item.update(
                status="failed",
                error={"name": type(error).__name__, "message": str(error)},
            )
            self.publish()
            raise
        finally:
            item["finished_at"] = now()
            await response.aclose()

    def failure(self, error):
        self.failed = self.failed or error
        if self.observations and self.observations[-1]["status"] in {
            "prepared",
            "received",
        }:
            self.observations[-1].update(
                status="failed",
                finished_at=now(),
                error={"name": type(error).__name__, "message": str(error)},
            )


async def create_client(
    key,
    evidence,
    *,
    transport=None,
    base_url="https://generativelanguage.googleapis.com",
):
    http = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(30, connect=5),
        follow_redirects=False,
        event_hooks={"request": [evidence.request], "response": [evidence.response]},
    )
    try:
        client = genai.Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(
                base_url=base_url,
                api_version="v1beta",
                timeout=30000,
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_async_client=http,
            ),
        )
        return client, http
    except BaseException:
        await http.aclose()
        raise
