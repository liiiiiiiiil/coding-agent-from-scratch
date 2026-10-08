"""Numeric-only diagnostics and workspace-relative process validation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent.context import count_tokens
from mini_agent.evaluation.reliability_diagnostics import RequestDiagnostics
from mini_agent.evaluation.reliability_worker import _fixture_process_directory, _reliability_protected_messages
from mini_agent.providers.base import UsageMeter, ProviderUsage


def test_numeric_diagnostics_partition_input_and_record_provider_delta():
    meter = UsageMeter()
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=meter), 20000, prior_tokens=5000)
    messages = [{"role": "system", "content": "PRIVATE_INSTRUCTIONS"},
                {"role": "system", "content": "[Structured State] state"},
                {"role": "system", "content": "[Runtime Notice] notice"},
                {"role": "assistant", "content": "PRIVATE_BODY", "reasoning_content": "PRIVATE_REASONING"}]
    schemas = [{"type": "function", "function": {"name": "fixture", "parameters": {}}}]
    runtime = SimpleNamespace(prepared_messages=messages, input_tokens=2000, output_tokens=300,
                              executor=SimpleNamespace(registry=SimpleNamespace(schemas=lambda: schemas)))
    reservation = count_tokens(messages) + count_tokens(schemas)
    diagnostic.before_request(runtime, reservation)
    diagnostic.output_limit(256)
    def client(messages):
        meter.record(ProviderUsage(900, 40, "provider"))
        return {"role": "assistant", "content": "PRIVATE_RESPONSE"}
    diagnostic.wrap_client(client)(messages)
    row = diagnostic.requests[0]
    assert sum(row["message_groups"].values()) == count_tokens(messages)
    assert sum(row["tool_schema_tokens_by_tool"].values()) == count_tokens(schemas)
    assert row["remaining_tokens_before"] == 12700
    assert row["input_tokens"] == 900 and row["output_tokens"] == 40
    assert row["usage_source"] == "provider"
    assert row["observed_input_to_estimate_ratio"] > 1
    assert "PRIVATE" not in json.dumps(diagnostic.snapshot(runtime))


def test_failed_unobserved_request_does_not_invent_zero_usage():
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=UsageMeter()), 100)
    runtime = SimpleNamespace(prepared_messages=[], input_tokens=0, output_tokens=0,
                              executor=SimpleNamespace(registry=SimpleNamespace(schemas=lambda: [])))
    diagnostic.before_request(runtime, 10)
    def client(messages):
        raise RuntimeError("PRIVATE_SECRET")
    with pytest.raises(RuntimeError):
        diagnostic.wrap_client(client)([])
    assert diagnostic.requests[0]["input_tokens"] is None
    assert diagnostic.requests[0]["usage_source"] == "unobserved"
    assert "PRIVATE" not in json.dumps(diagnostic.snapshot(runtime))


def test_refused_requests_are_bounded_and_overrun_is_explicit():
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=None), 100)
    runtime = SimpleNamespace(prepared_messages=[], input_tokens=110, output_tokens=10,
                              executor=SimpleNamespace(registry=SimpleNamespace(schemas=lambda: [])))
    for _ in range(80):
        diagnostic.before_request(runtime, 10)
        diagnostic.refuse("token_limit")
    assert len(diagnostic.requests) == 64
    assert diagnostic.snapshot(runtime)["budget_overrun_tokens"] == 20


def test_fixture_cwd_accepts_root_forms_rejects_outside_and_symlinks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for cwd in (None, ".", "./", str(workspace)):
        assert _fixture_process_directory(workspace, cwd) == workspace
    (workspace / "outside").symlink_to(tmp_path, target_is_directory=True)
    for cwd in ("..", "outside", str(tmp_path)):
        with pytest.raises(ValueError):
            _fixture_process_directory(workspace, cwd)
    with pytest.raises(FileNotFoundError):
        _fixture_process_directory(workspace, "missing")


def test_eval_protected_prompt_is_same_across_phases_without_ancestor_instructions(tmp_path, monkeypatch):
    from mini_agent.instructions import InstructionLoader
    monkeypatch.setattr(InstructionLoader, "load", lambda _: "PRIVATE_ANCESTOR_INSTRUCTIONS" * 500)
    before = _reliability_protected_messages(tmp_path)
    after = _reliability_protected_messages(tmp_path, recovery=True)
    assert after[0] == before[0]
    assert len(after) == 2
    assert "PRIVATE_ANCESTOR" not in json.dumps(after)
    assert "PermissionGate" in after[0]["content"]


def _budget_runtime(used=0):
    return SimpleNamespace(prepared_messages=[], input_tokens=used, output_tokens=0,
                           executor=SimpleNamespace(registry=SimpleNamespace(schemas=lambda: [])))


def test_request_reserves_calibrated_input_and_output_once():
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=None), 7000)
    runtime = _budget_runtime(1000)
    assert diagnostic.before_request(runtime, 2000)
    row = diagnostic.requests[-1]
    assert row["reserved_input_tokens_calibrated"] == 4256
    assert diagnostic.request_output_limit() == 1024
    runtime.input_tokens += 2000  # Canonical Runtime's pre-request accounting.
    assert diagnostic.request_output_limit() == 1024
    assert 1000 + 4256 + 1024 <= 7000


@pytest.mark.parametrize("used,reason", [(1490, "insufficient_request_budget"),
                                         (2000, "budget_exhausted"),
                                         (2001, "budget_already_exceeded")])
def test_budget_refusal_distinguishes_balance_and_overrun(used, reason):
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=None), 2000)
    assert not diagnostic.before_request(_budget_runtime(used), 100)
    assert diagnostic.requests[-1]["budget_refusal_reason"] == reason
    with pytest.raises(RuntimeError):
        diagnostic.request_output_limit()


def test_provider_positive_error_samples_survive_numeric_replay():
    prior = [{"usage_source": "provider", "reserved_input_tokens_estimated": 1000, "input_tokens": 3000},
             {"usage_source": "estimated", "reserved_input_tokens_estimated": 1, "input_tokens": 9999},
             {"usage_source": "provider", "reserved_input_tokens_estimated": 1000, "input_tokens": 100}]
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=None), 10000,
                                    prior_tokens=4000, prior_requests=prior)
    assert diagnostic.before_request(_budget_runtime(1000), 1000)
    assert diagnostic.input_multiplier == pytest.approx(3.3)
    assert diagnostic.requests[-1]["remaining_tokens_before"] == 5000
    assert diagnostic.requests[-1]["reserved_input_tokens_calibrated"] == 3556
    assert diagnostic.request_output_limit() == 1024


def test_live_batch_underestimated_input_is_refused_at_boundary():
    diagnostic = RequestDiagnostics(SimpleNamespace(usage_meter=None), 64000)
    assert not diagnostic.before_request(_budget_runtime(64000 - 6860), 5999)
    assert diagnostic.requests[-1]["reserved_input_tokens_calibrated"] == 12254
