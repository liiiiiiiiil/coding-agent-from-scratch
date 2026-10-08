"""Offline regression through the exact live worker assembly, with no provider I/O."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
import uuid

import pytest

from mini_agent.evaluation.reliability import validate_reliability
from mini_agent.evaluation.reliability_report import _scenario_report
from mini_agent.evaluation.reliability_schema import load_suite
from mini_agent.evaluation.reliability_worker import (
    _exception_diagnostic, _grade_workspace, _run_live, _run_public_test, _tool_call,
)


ROOT = Path(__file__).parent / "fixtures" / "evaluation" / "reliability"


def _worker(tmp_path, monkeypatch, scenario_id, client, binding=None):
    scenario = next(s for s in load_suite(ROOT / "suite.json").scenarios if s.scenario_id == scenario_id)
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / scenario_id / "initial", workspace)
    reference = {"profile": "fixture", "provider": "fixture", "protocol": "openai_chat", "fingerprint": "0" * 64}
    synthetic_usage = binding is None
    from mini_agent.providers.base import UsageMeter, ProviderUsage
    from mini_agent.budget import request_input_estimate
    from mini_agent.context import count_tokens
    binding = binding or SimpleNamespace(
        profile=SimpleNamespace(context_window=128_000, max_output_tokens=8192, model_id="PRIVATE_MODEL"),
        provider=SimpleNamespace(api_key="PRIVATE_KEY", endpoint="https://private.example/api", extra_headers={}),
        reference=SimpleNamespace(to_dict=lambda: reference, protocol="openai_chat"),
        usage_meter=UsageMeter(),
    )
    if synthetic_usage:
        original_client = client
        def client(messages, **options):
            reply = original_client(messages, **options)
            schemas = options["tool_registry"].schemas() if options.get("include_tools") else []
            raw = request_input_estimate(messages, schemas, model_id=binding.profile.model_id,
                stream=bool(options.get("stream_output")),
                max_output_tokens=options.get("max_output_tokens", binding.profile.max_output_tokens))
            binding.usage_meter.record(ProviderUsage(raw, count_tokens(reply), "provider"))
            return reply
    monkeypatch.setattr("mini_agent.evaluation.worker._llm_client", lambda *_: (client, binding, {}))
    return _run_live({
        "scenario": scenario.to_dict(), "workspace": str(workspace),
        "scenario_directory": str((ROOT / scenario_id).resolve()), "trial_id": str(uuid.uuid4()),
        "session_root": str(tmp_path / "sessions"), "crash_meta_path": str(tmp_path / "crash-meta.json"),
    })


def _response(name, arguments, call_id):
    return {"role": "assistant", "content": None, "tool_calls": [_tool_call(name, arguments, call_id)]}


def test_denied_write_runs_parent_policy_and_is_boundary_only(tmp_path, monkeypatch):
    calls = []
    def client(*_, **__):
        calls.append(True)
        return _response("write_file", {"path": "src/locked.py", "content": "def transform(value):\n    return value * 2\n"}, "denied-1")
    result = _worker(tmp_path, monkeypatch, "permission-denied", client)
    assert len(calls) == 1, "Permission refusal terminates the actual parent task."
    assert result["fault_status"] == "triggered"
    assert result["invariant_status"] == "passed", result
    assert result["agent_terminal_status"] == "failed"
    assert result["grader_passed"] is None
    assert result["recovery_status"] == "not_applicable"
    assert result["infrastructure_error"] is None
    assert (tmp_path / "workspace/src/locked.py").read_bytes() == (ROOT / "permission-denied/initial/src/locked.py").read_bytes()
    report = _scenario_report([result], 1, "live")
    assert report["boundary_only_trials"] == 1
    assert report["recovery_denominator"] == 0
    assert report["task_success_assessed"] is False
    assert report["task_grader_not_run"] == 0
    assert report["task_grader_not_applicable"] == 1


@pytest.mark.parametrize("cwd", [None, ".", "./", "absolute"])
def test_process_survives_slow_model_turn_then_stops_and_verifies(tmp_path, monkeypatch, cwd):
    turn = 0
    child_id = None
    def client(messages, **_):
        nonlocal turn, child_id
        turn += 1
        if turn == 1:
            arguments = {"command": "python support/process_fixture.py --wait-for-stop"}
            if cwd is not None:
                arguments["cwd"] = str(tmp_path / "workspace") if cwd == "absolute" else cwd
            return _response("start_process", arguments, "start-1")
        if turn == 2:
            time.sleep(1.1)  # Longer than the old fixture's entire lifetime.
            child_id = json.loads(next(m["content"] for m in reversed(messages) if m.get("role") == "tool"))["process_id"]
            return _response("wait_process", {"process_id": child_id, "timeout_ms": 40}, "wait-1")
        if turn == 3:
            return _response("terminate_process", {"process_id": child_id}, "stop-1")
        if turn == 4:
            return _response("write_file", {"path": "src/example.py", "content": 'def transform(value):\n    """Double any numeric input."""\n    result = value * 2\n    return result\n'}, "write-1")
        if turn == 5:
            return _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "verify-1")
        return {"role": "assistant", "content": "已完成", "tool_calls": []}
    result = _worker(tmp_path, monkeypatch, "process-wait-timeout", client)
    assert result["fault_status"] == "triggered", result
    assert result["invariant_status"] == "passed", result
    assert result["grader_passed"] is True, result
    assert result["recovery_status"] == "succeeded", result
    assert result["cleanup_complete"] is True
    assert result["trace_summary"]["events"]
    assert not result["state_summary"]["tool_diagnostics"]
    diagnostics = result["phases"]["agent"]["request_diagnostics"]
    assert diagnostics["requests"][0]["status"] == "responded"
    assert diagnostics["requests"][0]["tool_schema_tokens_estimated"] > 0


@pytest.mark.parametrize("scenario_id", ["tool-handler-exception", "verification-repair"])
def test_live_worker_recovers_real_failure_and_verifies(tmp_path, monkeypatch, scenario_id):
    turn = 0
    def client(messages, **_):
        nonlocal turn
        turn += 1
        if turn == 1:
            if scenario_id == "verification-repair":
                return _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "initial-verify")
            return _response("read_file", {"path": "src/example.py"}, "failed-read")
        if turn == 2:
            return _response("read_file", {"path": "src/example.py"}, "diagnose-read")
        if turn == 3:
            return _response("recover", {
                "action": "adjust", "caused_by_failure_id": "f-1", "reason": "已读取错误实现，改为数值翻倍",
                "requested_tool": "write_file", "requested_arguments": {
                    "path": "src/example.py", "content": "def transform(value):\n    return value + value\n",
                },
            }, "repair")
        if turn == 4:
            return _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "verify")
        return {"role": "assistant", "content": "已修复并验证", "tool_calls": []}
    result = _worker(tmp_path, monkeypatch, scenario_id, client)
    assert result["fault_status"] == "triggered", result
    assert result["invariant_status"] == "passed", result
    assert result["grader_passed"] is True, result
    assert result["recovery_status"] == "succeeded", result
    assert result["agent_terminal_status"] == "done", result
    if scenario_id == "tool-handler-exception":
        diagnostic = result["state_summary"]["tool_diagnostics"][0]
        assert diagnostic["stage"] == "tool_handler"
        assert diagnostic["frames"]


@pytest.mark.parametrize("scenario_id", [
    "tool-handler-exception", "verification-repair", "process-wait-timeout",
    "crash-after-handler", "mcp-disconnect", "subagent-timeout",
])
def test_full_grader_calibration_accepts_correct_behavior_and_rejects_initial(scenario_id):
    suite = load_suite(ROOT / "suite.json")
    scenario = next(s for s in suite.scenarios if s.scenario_id == scenario_id)
    directory = ROOT / scenario_id
    for phase, expected in (("initial", False), ("known_good", True)):
        assert _grade_workspace(scenario, directory / phase, directory / "grader.json") is expected
        assert _run_public_test(directory / phase, "python -m unittest discover -s tests") is expected


def test_independent_grader_accepts_equivalent_code_and_rejects_counterexample(tmp_path):
    suite = load_suite(ROOT / "suite.json")
    scenario = suite.scenarios[0]
    shutil.copytree(ROOT / scenario.scenario_id / "known_good", tmp_path / "workspace")
    target = tmp_path / "workspace/src/example.py"
    target.write_text('def transform(value):\n    """Equivalent implementation."""\n    return value + value\n')
    assert _grade_workspace(scenario, tmp_path / "workspace", ROOT / scenario.scenario_id / "grader.json") is True
    target.write_text('def transform(value):\n    return value * 2 if value > 0 else value\n')
    assert _grade_workspace(scenario, tmp_path / "workspace", ROOT / scenario.scenario_id / "grader.json") is False


def test_public_validation_rejects_missing_or_empty_test_suite(tmp_path):
    command = "python -m unittest discover -s tests"
    assert _run_public_test(tmp_path, command) is None
    (tmp_path / "tests").mkdir()
    assert _run_public_test(tmp_path, command) is False


def test_provider_diagnostic_redacts_values_and_keeps_safe_location():
    from mini_agent.providers.base import ProviderHTTPError
    binding = SimpleNamespace(
        provider=SimpleNamespace(api_key="PRIVATE_KEY", endpoint="https://private.example/api", extra_headers={"X-Private": "PRIVATE_HEADER"}),
        profile=SimpleNamespace(model_id="PRIVATE_MODEL"),
    )
    try:
        raise ProviderHTTPError(400, detail="bad schema: PRIVATE_MODEL PRIVATE_KEY https://private.example/api PRIVATE_HEADER")
    except ProviderHTTPError as error:
        detail = _exception_diagnostic(error, stage="agent", binding=binding)
    encoded = json.dumps(detail)
    assert detail["provider"]["status"] == 400
    assert "bad schema" in detail["provider"]["detail"]
    for secret in ("PRIVATE_MODEL", "PRIVATE_KEY", "PRIVATE_HEADER", "private.example"):
        assert secret not in encoded
    assert "Traceback" not in encoded


def test_review_distinguishes_boundary_case_and_full_public_calibration():
    result = validate_reliability(ROOT / "suite.json")
    for item in result["scenarios"]:
        if item["scenario_id"] == "permission-denied":
            assert item["assessment"] == "boundary_only"
        elif item["scenario_id"] in result["live_scenarios"]:
            assert item["public_test_calibration"] == {"initial_passed": False, "known_good_passed": True}


def test_request_reservation_stops_before_provider_call(tmp_path, monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("No provider request when pending input exceeds remaining budget")
    monkeypatch.setattr("mini_agent.budget.TaskBudgetController.input_reservation", lambda *_: 64_000)
    result = _worker(tmp_path, monkeypatch, "tool-handler-exception", forbidden)
    assert result["agent_stop_reason"] == "token_limit"
    assert result["state_summary"]["agent_error_kind"] is None
    row = result["phases"]["agent"]["request_diagnostics"]["requests"][0]
    assert row["status"] == "refused" and row["refusal_reason"] == "token_limit"
    assert row["input_tokens"] is None


@pytest.mark.parametrize("response_kind", ["tool", "text"])
def test_unexpected_provider_overrun_cannot_execute_or_finish(tmp_path, monkeypatch, response_kind):
    from mini_agent.providers.catalog import ProviderCatalog
    from mini_agent.providers.base import ProviderUsage
    catalog = ProviderCatalog.from_settings(
        {"fixture": {"protocol": "openai_chat", "endpoint": "https://fixture.invalid/chat/completions", "api_key": "fixture-key"}},
        {"fixture": {"provider_id": "fixture", "model_id": "fixture-model", "context_window": 128000, "max_output_tokens": 8192}},
        parent_profile="fixture", subagent_profile="fixture", subagent_allowed_profiles=("fixture",),
    )
    binding = catalog.parent_binding()
    def client(messages, **options):
        binding.usage_meter.record(ProviderUsage(64001, 100, "provider"))
        if response_kind == "text":
            return {"role": "assistant", "content": "已完成", "tool_calls": []}
        return _response("write_file", {"path": "src/example.py", "content": "must not be written"}, "overrun-write")
    result = _worker(tmp_path, monkeypatch, "tool-handler-exception", client, binding=binding)
    assert result["agent_stop_reason"] == "token_limit", result
    assert result["agent_terminal_status"] == "failed"
    assert result["state_summary"]["generation"] == 0
    assert all(a["handler_admitted"] is False for a in result["state_summary"]["attempts"])
    assert (tmp_path / "workspace/src/example.py").read_bytes() == (ROOT / "tool-handler-exception/initial/src/example.py").read_bytes()
    assert result["phases"]["agent"]["request_diagnostics"]["budget_overrun_tokens"] == 101


def test_worker_records_provider_failure_after_agent_started(tmp_path, monkeypatch):
    from mini_agent.providers.base import ProviderHTTPError
    def rejected(*_, **__):
        raise ProviderHTTPError(400, detail="invalid tool schema PRIVATE_MODEL PRIVATE_KEY")
    result = _worker(tmp_path, monkeypatch, "tool-handler-exception", rejected)
    assert result["infrastructure_error"] == "provider_http_400"
    assert result["agent_stop_reason"] == "agent_error"
    diagnostic = result["state_summary"]["diagnostic"]
    assert diagnostic["stage"] == "agent"
    assert diagnostic["provider"]["status"] == 400
    assert "PRIVATE" not in json.dumps(diagnostic)
    assert len(result["state_summary"]["tool_schema_sha256"]) == 64


def test_live_runner_calibrates_before_creating_run_or_resolving_credentials(tmp_path, monkeypatch):
    from mini_agent.evaluation.reliability import run_reliability
    def reject(path):
        assert path == (ROOT / "suite.json").resolve()
        raise ValueError("broken_public_calibration")
    monkeypatch.setattr("mini_agent.evaluation.reliability.validate_reliability", reject)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="broken_public_calibration"):
        run_reliability(ROOT / "suite.json", output, run_kind="live", live_confirmed=True)
    assert not output.exists()


def test_untriggered_process_boundary_is_incomplete_not_failed(tmp_path, monkeypatch):
    result = _worker(tmp_path, monkeypatch, "process-wait-timeout", lambda *_args, **_options: {
        "role": "assistant", "content": "无法继续", "tool_calls": [],
    })
    assert result["fault_status"] == "not_triggered"
    assert result["invariant_status"] == "incomplete"
    assert result["state_summary"]["pre_injection_stop_reason"] is not None


def test_subagent_timeout_reaches_injection_claims_and_recovers_without_provider_io(tmp_path, monkeypatch):
    from mini_agent.providers.catalog import ProviderCatalog, ModelBinding
    catalog = ProviderCatalog.from_settings(
        {"fixture": {"protocol": "openai_chat", "endpoint": "https://fixture.invalid/chat/completions", "api_key": "fixture-key"}},
        {"fixture": {"provider_id": "fixture", "model_id": "fixture-model", "context_window": 128000, "max_output_tokens": 8192}},
        parent_profile="fixture", subagent_profile="fixture", subagent_allowed_profiles=("fixture",),
    )
    monkeypatch.setattr(ProviderCatalog, "from_module", classmethod(lambda *_: catalog))
    def forbidden(*_args, **_options):
        raise AssertionError("The fixture must stop before provider I/O")
    monkeypatch.setattr(ModelBinding, "complete", forbidden)
    scenario = next(s for s in load_suite(ROOT / "suite.json").scenarios if s.scenario_id == "subagent-timeout")
    budget = scenario.fault["parameters"]["child_budget"]
    turn = 0
    child_id = None
    def client(messages, **_):
        nonlocal turn, child_id
        turn += 1
        if turn == 1:
            return _response("spawn_subagent", {
                "goal": "只读检查 transform", "scope": ["src/example.py", "tests/"],
                "constraints": [], "expected_findings": ["函数与测试是否一致"],
                "requested_tools": ["read_file", "list_dir", "grep"],
                "selected_parent_facts": [], "purpose": "investigation", "agent_profile": "general",
                "budget": budget,
            }, "spawn")
        if turn == 2:
            confirmation = json.loads(next(m["content"] for m in reversed(messages) if m.get("role") == "tool"))
            assert confirmation["accepted"] is True
            child_id = confirmation["child_session_id"]
            time.sleep(0.1)
            return _response("get_subagent_result", {"child_session_id": child_id}, "claim")
        if turn == 3:
            result = json.loads(next(m["content"] for m in reversed(messages) if m.get("role") == "tool"))
            assert result["outcome"] == "timed_out", result
            return _response("write_file", {"path": "src/example.py", "content": "def transform(value):\n    return value * 2\n"}, "write")
        if turn == 4:
            return _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "verify")
        return {"role": "assistant", "content": "完成", "tool_calls": []}
    result = _worker(tmp_path, monkeypatch, "subagent-timeout", client)
    assert result["fault_status"] == "triggered", result
    assert result["invariant_status"] == "passed", result
    assert result["recovery_status"] == "succeeded", result
    assert result["state_summary"]["injected_calls"][0]["counted_as_real"] is False


def test_crash_task_describes_before_and_after_injection_phases():
    scenario = next(s for s in load_suite(ROOT / "suite.json").scenarios if s.scenario_id == "crash-after-handler")
    assert "当前尚未崩溃" in scenario.task
    assert "首次 write_file 或 edit_file" in scenario.task
    assert "进入恢复会话后" in scenario.task


def test_crash_live_worker_writes_once_before_leaving_pending_boundary(tmp_path, monkeypatch):
    from mini_agent.session import SessionStore
    class InjectedCrash(BaseException):
        pass
    def crash(code):
        assert code == 86
        raise InjectedCrash()
    monkeypatch.setattr("mini_agent.evaluation.reliability_worker.os._exit", crash)
    def client(*_args, **_options):
        return _response("write_file", {"path": "src/example.py", "content": "def transform(value):\n    return value * 2\n"}, "crash-write")
    with pytest.raises(InjectedCrash):
        _worker(tmp_path, monkeypatch, "crash-after-handler", client)
    metadata = json.loads((tmp_path / "crash-meta.json").read_text())
    assert metadata["side_effect_count"] == 1
    assert len(metadata["fault_events"]) == 1
    assert (tmp_path / "workspace/src/example.py").read_text().endswith("return value * 2\n")
    boundary = SessionStore(tmp_path / "sessions").load(metadata["session_id"])["tool_boundary"]
    assert boundary["status"] == "pending"
    assert boundary["calls"][0]["handler_admitted"] is True


def test_timeout_contract_rejects_small_budget_and_retains_published_budget():
    from copy import deepcopy
    from mini_agent.evaluation.reliability_schema import validate_scenario
    scenario = next(s for s in load_suite(ROOT / "suite.json").scenarios if s.scenario_id == "subagent-timeout")
    for value in (4000, True):
        raw = deepcopy(scenario.to_dict())
        raw["fault"]["parameters"]["child_budget"]["max_tokens"] = value
        with pytest.raises(ValueError, match="child_budget"):
            validate_scenario(raw)


def test_invalid_process_directory_is_rejected_before_handler_admission(tmp_path, monkeypatch):
    def client(*_args, **_options):
        return _response("start_process", {"command": "python support/process_fixture.py --wait-for-stop", "cwd": ".."}, "invalid-start")
    result = _worker(tmp_path, monkeypatch, "process-wait-timeout", client)
    attempts = result["state_summary"]["attempts"]
    assert attempts
    assert all(a["handler_admitted"] is False for a in attempts)
    assert all(a["error_kind"] == "invalid_arguments" for a in attempts)
    assert result["state_summary"]["generation"] == 0
    assert result["fault_status"] == "not_triggered"
    assert result["cleanup_complete"] is True
    diagnostic = result["state_summary"]["tool_diagnostics"][0]
    assert diagnostic["stage"] == "tool_validation" and diagnostic["frames"]


def test_live_crash_resume_uses_eval_prompt_and_completes_new_verification(tmp_path, monkeypatch):
    from mini_agent.providers.catalog import ProviderCatalog, ModelBinding
    from mini_agent.evaluation.reliability_worker import _resume_live_crash
    from mini_agent.tools import plan
    catalog = ProviderCatalog.from_settings(
        {"fixture": {"protocol": "openai_chat", "endpoint": "https://fixture.invalid/chat/completions", "api_key": "fixture-key"}},
        {"fixture": {"provider_id": "fixture", "model_id": "fixture-model", "context_window": 128000, "max_output_tokens": 8192}},
        parent_profile="fixture", subagent_profile="fixture", subagent_allowed_profiles=("fixture",),
    )
    monkeypatch.setattr(ProviderCatalog, "from_module", classmethod(lambda *_: catalog))
    binding = catalog.parent_binding()
    class InjectedCrash(BaseException):
        pass
    def crash(_):
        raise InjectedCrash()
    monkeypatch.setattr("mini_agent.evaluation.reliability_worker.os._exit", crash)
    from mini_agent.providers.base import ProviderUsage
    pre_usage = []
    def first_client(messages, **options):
        from mini_agent.context import count_tokens
        actual = count_tokens(messages) + count_tokens(options["tool_registry"].schemas()) + 400
        pre_usage.append(actual + 100)
        binding.usage_meter.record(ProviderUsage(actual, 100, "provider"))
        return _response("write_file", {
            "path": "src/example.py", "content": "def transform(value):\n    return value * 2\n",
        }, "crash-write")
    with pytest.raises(InjectedCrash):
        _worker(tmp_path, monkeypatch, "crash-after-handler", first_client, binding=binding)
    original = plan.make_commit_plan_tool
    captured = {}
    def capture(state):
        captured["state"] = state
        return original(state)
    monkeypatch.setattr(plan, "make_commit_plan_tool", capture)
    monkeypatch.setattr("mini_agent.instructions.InstructionLoader.load", lambda _: "ANCESTOR_MARKER" * 1000)
    turn = 0
    def client(self, messages, **options):
        nonlocal turn
        turn += 1
        self.usage_meter.record(ProviderUsage(1000, 100, "provider"))
        assert "ANCESTOR_MARKER" not in json.dumps(messages)
        if turn == 1:
            return _response("commit_plan", {
                "goal": "验证崩溃前已写入的实现", "constraints": ["不重放未知副作用"],
                "success_criteria": ["独立测试通过"], "reason": "根据恢复调查重新规划",
                "trigger_id": captured["state"].planning_state.active_trigger_id,
                "steps": [{"step_id": "verify", "content": "运行独立验证", "depends_on": [],
                           "success_criteria": ["测试通过"], "replaces": []}],
            }, "plan")
        if turn == 2:
            return _response("update_plan_progress", {"revision_id": 1, "step_id": "verify", "status": "in_progress", "reason": "开始验证"}, "progress")
        if turn == 3:
            return _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "verify")
        if turn == 4:
            return _response("update_plan_progress", {"revision_id": 1, "step_id": "verify", "status": "completed", "reason": "独立测试通过"}, "complete")
        return {"role": "assistant", "content": "已完成", "tool_calls": []}
    monkeypatch.setattr(ModelBinding, "complete", client)
    scenario = next(s for s in load_suite(ROOT / "suite.json").scenarios if s.scenario_id == "crash-after-handler")
    result = _resume_live_crash({
        "scenario": scenario.to_dict(), "workspace": str(tmp_path / "workspace"),
        "scenario_directory": str((ROOT / "crash-after-handler").resolve()),
        "session_root": str(tmp_path / "sessions"), "crash_meta_path": str(tmp_path / "crash-meta.json"),
        "model_binding_ref": binding.reference.to_dict(),
    })
    assert result["invariant_status"] == "passed", result
    assert result["recovery_status"] == "succeeded", result
    assert result["phases"]["recovery"]["request_diagnostics"]["prior_phase_tokens"] == pre_usage[0]
    assert result["phases"]["pre_crash"]["request_diagnostics"]["requests"][0]["usage_source"] == "provider"
    assert len(result["phases"]["recovery"]["request_diagnostics"]["requests"]) == 5
    before = result["phases"]["pre_crash"]["request_diagnostics"]
    after = result["phases"]["recovery"]["request_diagnostics"]
    assert before["task_budget_ledger"]["samples"]
    key = before["requests"][0]["calibration_key"]
    assert after["task_budget_ledger"]["samples"][key][:1] == before["task_budget_ledger"]["samples"][key]
    assert after["requests"][0]["cumulative_tokens_before"] == pre_usage[0]


@pytest.mark.parametrize("mode,finish,expected", [
    ("json", "tool_calls", None), ("stream", "length", None),
    ("stream", "tool_calls", "provider_protocol_error"),
])
def test_tool_argument_failures_keep_generated_errors_in_recovery_score(tmp_path, monkeypatch, mode, finish, expected):
    from mini_agent.providers.base import ProviderToolArgumentsError
    calls = []
    def client(*_, **__):
        calls.append(True)
        if len(calls) == 1:
            return _response("read_file", {"path": "src/example.py"}, "injected")
        raise ProviderToolArgumentsError(argument_chars=32, json_position=31,
                                        response_mode=mode, finish_reason=finish)
    trial = _worker(tmp_path, monkeypatch, "tool-handler-exception", client)
    assert trial["fault_status"] == "triggered"
    assert trial["invariant_status"] == "passed"
    assert trial["infrastructure_error"] == expected
    report = _scenario_report([trial], 1, "live")
    assert report["recovery_denominator"] == (1 if expected is None else 0)
    assert report["recovery_successes"] == 0
    assert trial["recovery_status"] != "succeeded"
