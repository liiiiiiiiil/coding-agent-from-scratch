"""One-request, no-retry synthetic input-reservation probe for v0.52.

The synthetic content matches the archived #09 request's rough byte shape,
not its unsaved message body. No request text, response text, or credentials
are written to the result artifact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlsplit

from mini_agent import config
from mini_agent.budget import TaskBudgetController, request_input_estimate
from mini_agent.evaluation.benchmark import runtime_fingerprint
from mini_agent.providers.catalog import ProviderCatalog
from mini_agent.tools.base import Tool, ToolRegistry
if __package__:
    from . import probe_v052_provider as common
else:
    import probe_v052_provider as common


PREVIOUS_PROBES = tuple(f"provider-probe-20260930-{index:02d}" for index in range(1, 5))
OUTPUT_CAP = 1024  # Same request output ceiling as the frozen suite's work calls.


def synthetic_request() -> tuple[list[dict], ToolRegistry]:
    messages = [
        {"role": "system", "content": "诊断输入。" + "修" * 2502 + "x" * 4101},
        {"role": "user", "content": "只回复 OK，不调用工具。" + "u" * 63},
        {"role": "user", "content": "再次确认，只回复 OK。" + "u" * 63},
    ]
    registry = ToolRegistry()
    for index in range(11):
        registry.register(Tool(f"probe_{index}", "y" * 480,
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"], "additionalProperties": False},
            lambda text: "unused", effect_class="none"))
    return messages, registry


def previous_spend(baseline: Path) -> Decimal:
    total = Decimal(0)
    for name in PREVIOUS_PROBES:
        record = json.loads((baseline / name / "probe.json").read_text(encoding="utf-8"))
        if record.get("usd_cap") is None or record.get("binding_ref", {}).get("fingerprint") != common.EXPECTED_BINDING:
            raise ValueError("previous probe cohort or cost record changed")
        total += Decimal(record["upper_bound_spend_usd"])
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New probe directory")
    parser.add_argument("--total-cap-usd", required=True, type=Decimal)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.total_cap_usd <= 0 or args.total_cap_usd > Decimal("1"):
        raise ValueError("total cap must be in (0, 1] USD")

    binding = ProviderCatalog.from_module(config).parent_binding()
    hostname = (urlsplit(binding.provider.endpoint).hostname or "").lower()
    if (binding.reference.fingerprint != common.EXPECTED_BINDING or
            runtime_fingerprint() != common.EXPECTED_RUNTIME or
            binding.profile.model_id.lower() != "mimo-v2.6-flash" or
            hostname not in {"api.xiaomimimo.com", "api.mimo.mi.com"} or
            binding.reference.protocol != "openai_chat"):
        raise ValueError("reviewed binding or runtime changed; no provider request sent")

    baseline = Path(__file__).resolve().parents[1] / "docs/evaluation/baselines/v0.52"
    prior = previous_spend(baseline)
    worst = common.usd(common.MODEL_CONTEXT_CEILING, OUTPUT_CAP)
    if prior + worst > args.total_cap_usd:
        raise ValueError("cumulative worst-case cost exceeds total cap")
    messages, registry = synthetic_request()
    schemas = registry.schemas()
    raw = request_input_estimate(messages, schemas, model_id=binding.profile.model_id,
                                 stream=True, max_output_tokens=OUTPUT_CAP)
    request_shape = {"message_count": len(messages), "tool_schema_count": len(schemas),
                     "serialized_utf8_bytes": len(json.dumps({
                         "model": binding.profile.model_id, "messages": messages,
                         "stream": True, "max_tokens": OUTPUT_CAP, "tools": schemas,
                     }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                     "raw_input_estimate": raw, "cold_reserved_input": raw * 2 + 256}
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError("probe output already exists")
    output.mkdir(parents=True)
    artifact = output / "probe.json"
    record = {
        "format": "mini_agent.provider_large_input_probe", "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "dry_run" if not args.execute else "running",
        "cohort": "current_binding_independent_from_live_20260929_09",
        "binding_ref": binding.reference.to_dict(),
        "runtime_fingerprint": common.EXPECTED_RUNTIME,
        "price_source": common.PRICE_SOURCE,
        "usd_cap_total": str(args.total_cap_usd),
        "prior_probe_upper_bound_usd": str(prior),
        "worst_case_usd_this_request": str(worst),
        "request_shape": request_shape,
        "requests": [], "provider_requests_sent": 0,
        "upper_bound_spend_usd": "0", "actual_cost_usd_uncached_upper": "0",
        "live_slots_consumed": 0, "automatic_retries": 0,
    }
    common.persist(artifact, record)
    if not args.execute:
        return 0
    try:
        response, spent = common.send_one(binding, TaskBudgetController(64_000), record,
            artifact, "synthetic_large_input", messages, schemas, registry,
            Decimal(0), args.total_cap_usd - prior, output_cap=OUTPUT_CAP)
        if response is not None:
            usage = response.usage
            if usage.source == "provider":
                record["status"] = "completed"
                record["provider_input_to_raw_ratio"] = str(
                    Decimal(usage.input_tokens) / Decimal(raw))
                record["provider_input_within_cold_reservation"] = (
                    usage.input_tokens <= request_shape["cold_reserved_input"])
            else:
                record["status"] = "incomplete_provider_usage_missing"
        record["cumulative_upper_bound_spend_usd"] = str(prior + spent)
    finally:
        record["ended_at"] = datetime.now(timezone.utc).isoformat()
        common.persist(artifact, record)
    return 0 if record["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
