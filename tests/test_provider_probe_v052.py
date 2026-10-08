"""Offline checks for the capped three-call provider probe."""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import sys

from mini_agent.providers.base import ProviderResponse, ProviderUsage
from mini_agent.providers.catalog import ModelBindingRef
from scripts import probe_v052_provider as probe
from scripts import probe_v052_large_input as large_probe


def test_probe_worst_case_under_one_dollar_and_refuses_smaller_cap(tmp_path):
    assert probe.usd(probe.MODEL_CONTEXT_CEILING, probe.OUTPUT_CAP) * 3 == Decimal("0.42688128")
    assert probe.usd(1_000_000, 1_000_000) == Decimal("0.42")
    calls = []
    binding = SimpleNamespace(profile=SimpleNamespace(model_id="mimo-v2.6-flash"),
        reference=ModelBindingRef("fixture", "fixture", "openai_chat", "0" * 64),
        complete=lambda *_args, **_options: calls.append(True))
    record = {"status": "running", "requests": [], "provider_requests_sent": 0,
              "upper_bound_spend_usd": "0", "actual_cost_usd_uncached_upper": "0"}
    result, spent = probe.send_one(binding, probe.TaskBudgetController(64_000),
        record, tmp_path / "probe.json", "plain", [{"role": "user", "content": "hello"}], [],
        None, Decimal(0), Decimal("0.01"))
    assert result is None and spent == 0 and calls == []
    assert record["status"] == "usd_cap_preflight_refusal"


def test_probe_records_only_numbers_and_preserves_tool_pair(monkeypatch, tmp_path):
    reference = ModelBindingRef("fixture", "fixture", "openai_chat", probe.EXPECTED_BINDING)
    calls = []
    def complete(messages, **options):
        calls.append((messages, options))
        if len(calls) == 1:
            response = {"role": "assistant", "content": "OK"}
        elif len(calls) == 2:
            response = {"role": "assistant", "content": None, "reasoning_content": "PRIVATE_REASONING",
                "tool_calls": [{"id": "probe-call", "type": "function", "function": {
                    "name": "echo_probe", "arguments": '{"text":"PRIVATE_ARGUMENT"}'}}]}
        else:
            assert messages[-2]["role"] == "assistant"
            assert messages[-2]["reasoning_content"] == "PRIVATE_REASONING"
            assert messages[-1] == {"role": "tool", "tool_call_id": "probe-call",
                                    "content": "probe acknowledged"}
            response = {"role": "assistant", "content": "done"}
        schemas = options["tool_registry"].schemas() if options["include_tools"] else []
        raw = probe.request_input_estimate(messages, schemas,
            model_id="mimo-v2.6-flash", stream=True, max_output_tokens=probe.OUTPUT_CAP)
        return ProviderResponse(response, "stop", ProviderUsage(raw, 100, "provider"))
    binding = SimpleNamespace(
        profile=SimpleNamespace(model_id="mimo-v2.6-flash", max_output_tokens=8192),
        provider=SimpleNamespace(endpoint="https://api.xiaomimimo.com/v1/chat/completions"),
        reference=reference, complete=complete)
    monkeypatch.setattr(probe.ProviderCatalog, "from_module", lambda *_: SimpleNamespace(parent_binding=lambda: binding))
    monkeypatch.setattr(probe, "runtime_fingerprint", lambda: probe.EXPECTED_RUNTIME)
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(tmp_path / "new"),
                                     "--cap-usd", "1", "--output-cap", "1024", "--execute"])
    assert probe.main() == 0
    row = json.loads((tmp_path / "new" / "probe.json").read_text())
    assert row["status"] == "completed" and row["provider_requests_sent"] == 3
    assert row["requested_output_cap"] == 1024
    assert len(row["requests"]) == 3 and all(r["status"] == "responded" for r in row["requests"])
    assert all(r["provider_input"] > 0 and r["provider_output"] == 100 for r in row["requests"])
    raw = (tmp_path / "new" / "probe.json").read_text()
    assert "PRIVATE_" not in raw and "api.xiaomimimo.com" not in raw


def test_probe_protocol_error_records_location_without_response_text(tmp_path):
    from mini_agent.providers.base import ProviderProtocolError
    def fail(*_args, **_options):
        raise ProviderProtocolError("PRIVATE_RESPONSE_BODY")
    binding = SimpleNamespace(profile=SimpleNamespace(model_id="mimo-v2.6-flash"),
        reference=ModelBindingRef("fixture", "fixture", "openai_chat", "0" * 64),
        complete=fail)
    record = {"status": "running", "requests": [], "provider_requests_sent": 0,
              "upper_bound_spend_usd": "0", "actual_cost_usd_uncached_upper": "0"}
    result, spent = probe.send_one(binding, probe.TaskBudgetController(64_000),
        record, tmp_path / "probe.json", "plain", [{"role": "user", "content": "hello"}], [],
        None, Decimal(0), Decimal("1"))
    assert result is None and spent == probe.usd(probe.MODEL_CONTEXT_CEILING, probe.OUTPUT_CAP)
    row = json.loads((tmp_path / "probe.json").read_text())["requests"][0]
    assert row["diagnostic"]["error_kind"] == "ProviderProtocolError"
    assert "PRIVATE_RESPONSE_BODY" not in (tmp_path / "probe.json").read_text()


def test_large_input_shape_and_cumulative_cap_preflight(tmp_path):
    messages, registry = large_probe.synthetic_request()
    schemas = registry.schemas()
    assert len(messages) == 3 and len(schemas) == 11
    raw = probe.request_input_estimate(messages, schemas, model_id="mimo-v2.6-flash",
                                        stream=True, max_output_tokens=large_probe.OUTPUT_CAP)
    assert raw * 2 + 256 >= 13748
    assert probe.usd(probe.MODEL_CONTEXT_CEILING, large_probe.OUTPUT_CAP) == Decimal("0.14028672")
    baseline = tmp_path / "baseline"
    for name in large_probe.PREVIOUS_PROBES:
        path = baseline / name
        path.mkdir(parents=True)
        (path / "probe.json").write_text(json.dumps({"usd_cap": "1",
            "binding_ref": {"fingerprint": probe.EXPECTED_BINDING},
            "upper_bound_spend_usd": "0.1"}))
    assert large_probe.previous_spend(baseline) == Decimal("0.4")
