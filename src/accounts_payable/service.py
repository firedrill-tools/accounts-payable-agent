"""Owned invoice processing; human approval and posting are separate API calls."""

import asyncio
import os
import time
from google.adk.models.base_llm import BaseLlm
from .agent import ADKInvoiceExtractor, INSTRUCTIONS
from .evidence import Receipt
from .schemas import identifier, sha


def process_invoice(store, source_id, model=None, **options):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(process_invoice_async(store, source_id, model, **options))
    raise RuntimeError("use process_invoice_async inside an active event loop")


async def process_invoice_async(
    store,
    source_id,
    model=None,
    *,
    transport=None,
    base_url="https://generativelanguage.googleapis.com",
    timeout_seconds=120,
    output_root=None,
    publish=None,
):
    identifier(source_id, "source ID")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= 120
    ):
        raise ValueError("timeout must be positive and no more than 120 seconds")
    receipt = None
    claim = None
    extractor = None
    error = None
    result = None
    active = True
    deadline = time.monotonic() + timeout_seconds
    model_name = (
        model.model
        if isinstance(model, BaseLlm)
        else model or os.getenv("GEMINI_MODEL")
    )

    def guard():
        if receipt:
            receipt.guard()
        if not active:
            raise RuntimeError("invoice processing run is closed")
        if time.monotonic() >= deadline:
            raise TimeoutError("invoice processing deadline expired before publication")
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError()

    try:
        async with asyncio.timeout(timeout_seconds):
            receipt = Receipt(
                store,
                source_id,
                model_name,
                output_root,
                **({"writer": publish} if publish is not None else {}),
            )
            receipt.save()
            guard()
            origin = (
                "caller_injected_model"
                if isinstance(model, BaseLlm)
                else "model_extraction"
            )
            claim = store.claim_extraction(
                source_id, receipt.data["run_id"], receipt.path, origin
            )
            receipt.data["source"] = claim["source"]
            receipt.data["claim"] = (
                {"token": claim["token"], "owner_pid": os.getpid()}
                if claim["token"]
                else None
            )
            receipt.data["instructions_sha256"] = sha(INSTRUCTIONS.encode())
            if claim["cached"] is not None:
                receipt.data["execution_kind"] = "model_free_existing_invoice"
                receipt.save()
                guard()
                invoice = claim["cached"]
                cached = True
            else:
                receipt.data["execution_kind"] = (
                    "caller_injected_model"
                    if isinstance(model, BaseLlm)
                    else "native_adk_gemini"
                )
                receipt.save()
                guard()
                extractor = ADKInvoiceExtractor(
                    model,
                    transport=transport,
                    base_url=base_url,
                    timeout_seconds=max(0.001, deadline - time.monotonic()),
                )
                extracted = await extractor.extract(
                    claim["source"]["body"], on_observation=receipt.observe, guard=guard
                )
                receipt.observe(extracted.observation)
                receipt.data["publication_status"] = "prepared"
                receipt.save()
                guard()
                try:
                    invoice = store.record_facts(
                        source_id,
                        extracted.facts,
                        expected_source_sha=claim["source"]["sha256"],
                        origin=origin,
                        run_id=receipt.data["run_id"],
                        lease_token=claim["token"],
                        commit_guard=guard,
                    )
                    receipt.data["publication_status"] = "committed"
                except BaseException:
                    receipt.data["publication_status"] = "unknown_or_failed"
                    raise
                cached = False
            result = {
                "invoice": invoice,
                "cached": cached,
                "execution_kind": receipt.data["execution_kind"],
                "reported_usage": receipt.data["reported_usage"],
                "cost_usd": None,
                "run_id": receipt.data["run_id"],
                "receipt_path": str(receipt.path),
            }
    except BaseException as caught:
        error = caught
        observation = getattr(caught, "model_observation", None)
        if receipt and observation is not None:
            receipt.data["model_observation"] = observation
            receipt.data["reported_usage"] = observation["reported_usage"]
    finally:
        active = False
        if extractor:
            try:
                await extractor.close()
            except BaseException as caught:
                error = error or caught
                if receipt:
                    receipt.data["cleanup_errors"].append(
                        f"{type(caught).__name__}: {caught}"
                    )
        if claim and claim["token"]:
            try:
                store.release_extraction(source_id, claim["token"], error)
            except BaseException as caught:
                error = error or caught
                if receipt:
                    receipt.data["cleanup_errors"].append(
                        f"{type(caught).__name__}: {caught}"
                    )
        if receipt:
            try:
                receipt.finish(result, error)
            except BaseException as caught:
                receipt.data.update(
                    status="failed",
                    result=None,
                    evidence_error=f"{type(caught).__name__}: {caught}",
                )
                error = error or caught
    if error is not None:
        error.evidence = receipt.data if receipt else None
        error.receipt_path = str(receipt.path) if receipt else None
        raise error
    return result
