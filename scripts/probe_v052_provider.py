"""Three-call, no-retry provider accounting probe; never archives request text.

Run only for the reviewed MiMo-V2.6-Flash first-party binding. The rate card
is https://mimo.mi.com/docs/pricing (overseas real-time API, 2026-09-30).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from mini_agent import config
from mini_agent.budget import TaskBudgetController, request_input_estimate, request_key
from mini_agent.evaluation.benchmark import runtime_fingerprint
from mini_agent.providers.catalog import ProviderCatalog
from mini_agent.tools.base import Tool, ToolRegistry


PRICE_SOURCE = "https://mimo.mi.com/docs/pricing"
INPUT_USD_PER_MILLION = Decimal("0.14")  # Charge all input as cache misses.
OUTPUT_USD_PER_MILLION = Decimal("0.28")
CACHED_INPUT_USD_PER_MILLION = Decimal("0.0028")
MODEL_CONTEXT_CEILING = 1_000_000  # Official model specification.
OUTPUT_CAP = 8192  # Match the reviewed local profile during the tool-call probe.
REQUEST_COUNT = 3
EXPECTED_BINDING = "fbfec44601e7b2c8677e7e01055d8cb63871c1a591454fba9058f97272d68e8e"
EXPECTED_RUNTIME = "90f115c278221bcd42638d2ee0b8aa3640774abaaa25ddb05482f4bd2c34a468"


def usd(input_tokens: int, output_tokens: int) -> Decimal:
    return ((Decimal(input_tokens) * INPUT_USD_PER_MILLION +
             Decimal(output_tokens) * OUTPUT_USD_PER_MILLION) / 1_000_000)


def persist(path: Path, record: dict) -> None:
    staging = path.with_suffix(".json.tmp")
    staging.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New, empty probe directory")
    parser.add_argument("--cap-usd", required=True, type=Decimal)
    parser.add_argument("--output-cap", type=int, default=OUTPUT_CAP)
    parser.add_argument("--execute", action="store_true", help="Send up to three paid requests")
    args = parser.parse_args()
    if args.cap_usd <= 0 or args.cap_usd > Decimal("1"):
        raise ValueError("probe cap must be between 0 and 1 USD")
    if args.output_cap < 256 or args.output_cap > OUTPUT_CAP:
        raise ValueError("output cap must be between 256 and the reviewed profile cap")

    binding = ProviderCatalog.from_module(config).parent_binding()
    hostname = (urlsplit(binding.provider.endpoint).hostname or "").lower()
    if (binding.reference.fingerprint != EXPECTED_BINDING or
            runtime_fingerprint() != EXPECTED_RUNTIME or
            binding.profile.model_id.lower() != "mimo-v2.6-flash" or
            hostname not in {"api.xiaomimimo.com", "api.mimo.mi.com"} or
            binding.reference.protocol != "openai_chat"):
        raise ValueError("reviewed binding or runtime changed; no provider request sent")

    worst_one = usd(MODEL_CONTEXT_CEILING, args.output_cap)
    worst_all = worst_one * REQUEST_COUNT
    if worst_all > args.cap_usd:
        raise ValueError("USD cap cannot admit the three-request worst-case envelope")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError("probe output already exists")
    output.mkdir(parents=True)
    artifact = output / "probe.json"
    record = {
        "format": "mini_agent.provider_accounting_probe", "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "dry_run" if not args.execute else "running",
        "cohort": "current_binding_independent_from_live_20260929_09",
        "binding_ref": binding.reference.to_dict(),
        "runtime_fingerprint": EXPECTED_RUNTIME,
        "price_source": PRICE_SOURCE,
        "rate_usd_per_million": {
            "input_cache_hit": str(CACHED_INPUT_USD_PER_MILLION),
            "input_cache_miss": str(INPUT_USD_PER_MILLION),
            "output": str(OUTPUT_USD_PER_MILLION),
        },
        "usd_cap": str(args.cap_usd),
        "worst_case_usd_per_request": str(worst_one),
        "worst_case_usd_all_requests": str(worst_all),
        "requested_output_cap": args.output_cap,
        "requests": [], "provider_requests_sent": 0,
        "upper_bound_spend_usd": "0", "actual_cost_usd_uncached_upper": "0",
        "live_slots_consumed": 0, "automatic_retries": 0,
    }
    persist(artifact, record)
    if not args.execute:
        return 0

    registry = ToolRegistry()
    registry.register(Tool("echo_probe", "Return the probe acknowledgement.",
        {"type": "object", "properties": {"text": {"type": "string"}},
         "required": ["text"], "additionalProperties": False},
        lambda text: "probe acknowledged", effect_class="none"))
    controller = TaskBudgetController(64_000)
    spent_upper = Decimal(0)
    rows = [
        ("plain", [{"role": "user", "content": "Reply with exactly OK."}], []),
        ("tool_call", [{"role": "user", "content":
            "Call echo_probe exactly once with text ping; do not answer in prose."}],
         registry.schemas()),
    ]
    try:
        for index in range(REQUEST_COUNT):
            if index == 2:
                break  # The third request is built from the actual paired tool response below.
            kind, messages, schemas = rows[index]
            response, spent_upper = send_one(binding, controller, record, artifact,
                kind, messages, schemas, registry, spent_upper, args.cap_usd,
                output_cap=args.output_cap)
            if response is None:
                break
            if index == 1:
                calls = response.message.get("tool_calls") or []
                if not 1 <= len(calls) <= 4:
                    record["status"] = "incomplete_no_bounded_tool_calls"
                    persist(artifact, record)
                    break
                followup = list(messages) + [response.message] + [
                    {"role": "tool", "tool_call_id": call["id"], "content": "probe acknowledged"}
                    for call in calls]
                response, spent_upper = send_one(binding, controller, record, artifact,
                    "tool_result_followup", followup, [], registry, spent_upper, args.cap_usd,
                    output_cap=args.output_cap)
                break
    finally:
        if record["status"] == "running":
            record["status"] = "completed" if record["provider_requests_sent"] == 3 else "incomplete"
        record["ended_at"] = datetime.now(timezone.utc).isoformat()
        persist(artifact, record)
    return 0 if record["status"] == "completed" else 2


def send_one(binding, controller, record, artifact, kind, messages, schemas,
             registry, spent_upper, cap, *, output_cap=OUTPUT_CAP):
    worst_one = usd(MODEL_CONTEXT_CEILING, output_cap)
    if spent_upper + worst_one > cap:
        record["status"] = "usd_cap_preflight_refusal"
        persist(artifact, record)
        return None, spent_upper
    raw = request_input_estimate(messages, schemas,
        model_id=binding.profile.model_id, stream=True, max_output_tokens=output_cap)
    key = request_key(binding.reference, schemas, "work",
                      estimator="openai_chat_utf8_request_v1")
    proposal = controller.proposal(raw, key, "work", output_cap)
    if not proposal["admitted"]:
        record["status"] = "token_budget_preflight_refusal"
        persist(artifact, record)
        return None, spent_upper
    request_id = controller.reserve(proposal)
    row = {"request_index": len(record["requests"]) + 1, "kind": kind,
           "status": "pending", "raw_input_estimate": raw,
           "reserved_input": proposal["input"], "output_cap": output_cap,
           "provider_input": None, "provider_output": None,
           "usage_source": "unobserved", "usd_upper": str(worst_one)}
    record["requests"].append(row)
    record["provider_requests_sent"] += 1
    record["upper_bound_spend_usd"] = str(spent_upper + worst_one)
    persist(artifact, record)  # A crash after dispatch remains a charged unknown call.
    try:
        response = binding.complete(messages, include_tools=bool(schemas),
                                    tool_registry=registry if schemas else None,
                                    stream_output=True, max_output_tokens=output_cap)
    except Exception as error:
        from mini_agent.providers.base import ProviderHTTPError
        from mini_agent.evaluation.reliability_worker import _exception_diagnostic
        controller.settle(request_id)
        row.update(status="provider_error", error_kind=type(error).__name__)
        row["diagnostic"] = _exception_diagnostic(error, stage="provider_probe")
        if isinstance(error, ProviderHTTPError):
            row["http_status"] = error.status
        record["status"] = "provider_error"
        persist(artifact, record)
        return None, spent_upper + worst_one
    usage = response.usage
    if usage.source == "provider":
        controller.settle(request_id, usage.input_tokens, usage.output_tokens, provider=True)
        cost = usd(usage.input_tokens, usage.output_tokens)
        row.update(provider_input=usage.input_tokens, provider_output=usage.output_tokens,
                   usage_source="provider", usd_upper=str(cost))
        spent_upper += cost
    else:
        controller.settle(request_id)
        row["usage_source"] = "unknown_charged_worst_case"
        spent_upper += worst_one
    row["status"] = "responded"
    record["upper_bound_spend_usd"] = str(spent_upper)
    record["actual_cost_usd_uncached_upper"] = str(spent_upper)
    if (controller.data["overrun_tokens"] or
            usage.source == "provider" and
            (usage.input_tokens > MODEL_CONTEXT_CEILING or usage.output_tokens > output_cap)):
        row["status"] = "provider_usage_exceeded_local_envelope"
        record["status"] = "provider_usage_exceeded_local_envelope"
    persist(artifact, record)
    return (None if record["status"] != "running" else response), spent_upper


if __name__ == "__main__":
    raise SystemExit(main())
