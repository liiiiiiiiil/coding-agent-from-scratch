from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mini_agent.evaluation.reliability import (
    PINNED_SUITE_FINGERPRINT, reliability_plan, validate_reliability,
)
from mini_agent.evaluation.reliability_schema import (
    SCENARIO_IDS, load_suite, material_tree_sha256, validate_scenario,
)
from mini_agent.evaluation.faults import FaultController


SUITE_PATH = Path(__file__).parent / "fixtures" / "evaluation" / "reliability" / "suite.json"


def test_frozen_suite_contains_all_scenarios_and_explicit_slot_variants():
    suite = load_suite(SUITE_PATH)
    assert suite.suite_id == "reliability-boundaries"
    assert suite.version == "1.6"
    assert suite.suite_sha256 == PINNED_SUITE_FINGERPRINT
    assert tuple(item.scenario_id for item in suite.scenarios) == SCENARIO_IDS
    assert len(reliability_plan(suite, run_kind="fixture")) <= 64
    live = reliability_plan(suite, run_kind="live", repeats=3)
    assert len(live) == 21
    assert [item["scenario_id"] for item in live] == [sid for sid in suite.live_scenario_ids for _ in range(3)]
    mcp = [item for item in reliability_plan(suite, run_kind="fixture")
           if item["scenario_id"].startswith("mcp-")]
    assert {item["variant"]["transport"] for item in mcp} == {"stdio", "loopback_http"}
    assert {item["variant"]["transport"] for item in mcp if item["scenario_id"] == "mcp-disconnect"} == {
        "stdio", "loopback_http",
    }


def test_validation_print_contract_includes_reviewable_tasks_permissions_and_feedback():
    value = validate_reliability(SUITE_PATH)
    assert value["valid"] is True
    assert value["scenario_count"] == 18
    by_id = {item["scenario_id"]: item for item in value["scenarios"]}
    assert "受控异常" in by_id["tool-handler-exception"]["task"]
    denied_patterns = {rule["pattern"] for rule in by_id["permission-denied"]["permission_rules"]
                       if rule["action"] == "deny" and rule["tool"] == "write_file"}
    assert denied_patterns == {"*"}
    assert by_id["crash-after-handler"]["feedback_strategy"]
    assert "task-transform-doubles" in by_id["tool-handler-exception"]["grader_checks"]
    assert by_id["tool-handler-exception"]["grader_calibration"] == {
        "initial_passed": False, "known_good_passed": True,
    }
    for scenario_id in value["live_scenarios"]:
        scenario = by_id[scenario_id]
        if scenario_id == "permission-denied":
            assert scenario["assessment"] == "boundary_only"
            assert "run_shell" not in scenario["allowed_tools"]
            continue
        assert {"request_replan", "recover"} <= set(scenario["allowed_tools"])
        assert all(any(rule["tool"] == name and rule["action"] == "allow"
                       for rule in scenario["permission_rules"])
                   for name in ("request_replan", "recover"))
        assert "python -m unittest discover -s tests" in scenario["task"]
    assert "先调用 mcp_reliability_echo 一次" in by_id["mcp-disconnect"]["task"]
    assert "timeout_ms=40" in by_id["process-wait-timeout"]["task"]
    assert "--wait-for-stop" in by_id["process-wait-timeout"]["task"]


def test_permission_trial_denies_relative_and_absolute_target_paths():
    from mini_agent.evaluation.worker import NonInteractivePermissionGate
    from mini_agent.permission import PermissionPolicy

    raw = json.loads((SUITE_PATH.parent / "permission-denied" / "scenario.json").read_text())
    rule_map = {}
    for rule in raw["permission_rules"]:
        rule_map.setdefault(rule["tool"], {})[rule["pattern"]] = rule["action"]
    gate = NonInteractivePermissionGate(PermissionPolicy(rule_map))
    assert gate.guard("write_file", {"path": "src/locked.py"})
    assert gate.guard("write_file", {"path": "/trial/workspace/src/locked.py"})
    assert gate.guard("write_file", {"path": "src/fallback.py"})


def test_scenario_contract_rejects_unknown_fields_and_unsafe_targets():
    raw = json.loads((SUITE_PATH.parent / "tool-handler-exception" / "scenario.json").read_text())
    unknown = dict(raw, arbitrary_module="os")
    with pytest.raises(ValueError, match="未知字段"):
        validate_scenario(unknown, expected_id="tool-handler-exception")
    traversal = json.loads(json.dumps(raw))
    traversal["fault"]["target"] = "../outside"
    with pytest.raises(ValueError):
        validate_scenario(traversal, expected_id="tool-handler-exception")


def test_grader_contract_cannot_execute_python_and_material_hash_is_checked(tmp_path):
    fixture = SUITE_PATH.parent / "tool-handler-exception"
    frozen_digest = next(
        item["materials_sha256"] for item in load_suite(SUITE_PATH).scenario_entries
        if item["scenario_id"] == "tool-handler-exception"
    )
    assert material_tree_sha256(fixture) == frozen_digest
    copied_fixture = tmp_path / "fixture"
    shutil.copytree(fixture, copied_fixture)
    cache_dir = copied_fixture / "support" / "__pycache__"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "fixture.cpython-314.pyc").write_bytes(b"runtime cache")
    assert material_tree_sha256(copied_fixture) == frozen_digest
    copied = tmp_path / "suite"
    copied.mkdir()
    (copied / "grader.py").write_text("import os\nGRADER = {}\n", encoding="utf-8")
    from mini_agent.evaluation.reliability_schema import _validate_grader_source
    with pytest.raises(ValueError, match="字面量"):
        _validate_grader_source(copied / "grader.py", "tool-handler-exception")


def test_frozen_run_copy_omits_runtime_python_caches(tmp_path):
    from mini_agent.evaluation.reliability import _copy_frozen_materials

    suite = load_suite(SUITE_PATH)
    target = tmp_path / "run"
    target.mkdir()
    _copy_frozen_materials(suite, target)
    frozen = target / "frozen"
    assert not list(frozen.rglob("__pycache__"))
    assert not [path for path in frozen.rglob("*") if path.suffix in {".pyc", ".pyo"}]


def test_fault_controller_records_only_the_named_number_of_hits():
    controller = FaultController({
        "fault_id": "tool-handler-exception", "target": "handler.read_file", "occurrences": 1,
    })
    controller.hit("handler.write_file")
    assert controller.hit_count == 0
    assert controller.hit("handler.read_file", evidence={"call": 1}) is True
    assert controller.hit("handler.read_file", evidence={"call": 2}) is False
    assert len(controller.events) == 1


def test_live_prompt_uses_the_trial_workspace_and_keeps_default_compatible(tmp_path, monkeypatch):
    from mini_agent.prompt import build_system_prompt

    monkeypatch.chdir(tmp_path)
    trial_workspace = tmp_path / "isolated" / "trial"
    trial_workspace.mkdir(parents=True)
    prompt = build_system_prompt(cwd=str(trial_workspace))
    default_prompt = build_system_prompt()
    assert f"Working directory: {trial_workspace}" in prompt
    assert f"Working directory: {tmp_path}" in default_prompt
    assert "Plan Contract" in prompt
    assert "verification" in prompt


def test_live_declared_plan_tools_are_registered_only_when_frozen_surface_allows_them():
    from mini_agent.evaluation.reliability_worker import _register_declared_plan_tools
    from mini_agent.state import AgentState
    from mini_agent.tools.base import ToolRegistry

    state = AgentState()
    registry = ToolRegistry()
    _register_declared_plan_tools(registry, state, ["read_file", "commit_plan", "request_replan"])
    assert [tool.name for tool in registry.list_tools()] == ["commit_plan", "request_replan"]

    empty = ToolRegistry()
    _register_declared_plan_tools(empty, state, ["read_file"])
    assert empty.list_tools() == []


def test_live_recovery_tool_uses_canonical_runtime_and_is_opt_in():
    from mini_agent.evaluation.reliability_worker import _register_declared_recovery_tool
    from mini_agent.state import AgentState
    from mini_agent.tools.base import ToolRegistry

    state = AgentState()
    registry = ToolRegistry()
    assert _register_declared_recovery_tool(registry, state, ["read_file"]) is None
    assert registry.list_tools() == []
    runtime = _register_declared_recovery_tool(registry, state, ["recover"])
    assert type(runtime).__name__ == "RecoveryRuntime"
    assert [tool.name for tool in registry.list_tools()] == ["recover"]


def test_live_fault_hooks_require_real_failure_or_exact_denied_target():
    from mini_agent.evaluation.reliability_worker import (
        _record_denied_write_fault, _record_initial_verification_fault,
    )

    verification = FaultController({
        "fault_id": "verification-repair", "target": "grader.public_test_failure",
        "occurrences": 1,
    })
    assert not _record_initial_verification_fault(verification, None)
    assert not _record_initial_verification_fault(verification, True)
    assert _record_initial_verification_fault(verification, False)
    assert verification.hit_count == 1

    permission = FaultController({
        "fault_id": "permission-denied", "target": "permission.write_file.src/locked.py",
        "occurrences": 1,
    })
    assert not _record_denied_write_fault(
        permission, scenario_id="permission-denied", tool_name="write_file",
        arguments={"path": "src/other.py"}, message="权限拒绝",
    )
    assert not _record_denied_write_fault(
        permission, scenario_id="permission-denied", tool_name="write_file",
        arguments={"path": "src/locked.py"}, message=None,
    )
    assert _record_denied_write_fault(
        permission, scenario_id="permission-denied", tool_name="write_file",
        arguments={"path": "/trial/src/locked.py"}, message="权限拒绝: 评测策略禁止调用 write_file",
    )
    assert permission.hit_count == 1


def test_provider_failures_map_to_safe_infrastructure_categories():
    from mini_agent.evaluation.reliability_worker import _provider_infrastructure_category
    from mini_agent.providers.base import ProviderConnectionError, ProviderHTTPError, ProviderProtocolError

    assert _provider_infrastructure_category(ProviderConnectionError("https://secret.invalid key=secret")) == "provider_connection_error"
    assert _provider_infrastructure_category(ProviderHTTPError(429, "Too Many Requests", "private response")) == "provider_http_429"
    assert _provider_infrastructure_category(ProviderProtocolError("private response body")) == "provider_protocol_error"
    assert _provider_infrastructure_category(RuntimeError("agent failure")) is None


def test_live_private_values_are_scrubbed_from_generated_trial_artifacts(tmp_path):
    from mini_agent.evaluation.reliability import (
        _build_worker_request, _redact_artifact_value, _redact_trial_artifacts,
    )

    trial = tmp_path / "trial"
    (trial / "sessions").mkdir(parents=True)
    (trial / "workspace").mkdir()
    (trial / "sessions" / "history.json").write_text(
        '{"endpoint":"https://provider.example/v1","note":"api_key=secret-value"}',
        encoding="utf-8",
    )
    (trial / "workspace" / "source.py").write_text("public fixture text", encoding="utf-8")
    redactions = ("https://provider.example/v1", "secret-value")

    _redact_trial_artifacts(trial, redactions)
    safe = (trial / "sessions" / "history.json").read_text(encoding="utf-8")
    assert "https://provider.example/v1" not in safe
    assert "secret-value" not in safe
    assert "<redacted>" in safe
    assert (trial / "workspace" / "source.py").read_text(encoding="utf-8") == "public fixture text"
    assert _redact_artifact_value({"result": "secret-value"}, redactions) == {
        "result": "<redacted>"
    }

    suite = load_suite(SUITE_PATH)
    scenario = suite.scenarios[0]
    request = _build_worker_request(
        suite=suite, scenario=scenario,
        slot={"run_kind": "fixture", "repetition": 1, "variant": {}},
        trial_id="trial", run_root=tmp_path, trial_dir=trial,
        live_ref={"provider": "local", "profile": "default"},
        code_revision="revision", runtime_fingerprint_value="fingerprint",
    )
    serialized_request = json.dumps(request)
    assert "redactions" not in request
    assert "https://provider.example/v1" not in serialized_request
    assert "secret-value" not in serialized_request


def test_live_run_refuses_before_creating_output_without_explicit_flag(tmp_path):
    from mini_agent.evaluation.reliability import run_reliability

    output = tmp_path / "live"
    with pytest.raises(ValueError, match="--live"):
        run_reliability(SUITE_PATH, output, run_kind="live", live_confirmed=False)
    assert not output.exists()


def test_fixture_component_probes_do_not_count_as_task_recovery_success():
    from mini_agent.evaluation.reliability_report import _scenario_report

    row = {
        "fault_status": "triggered", "invariant_status": "passed",
        "recovery_status": "succeeded", "grader_passed": True,
        "source_consistent": True, "infrastructure_error": None,
        "agent_stop_reason": "text", "agent_terminal_status": "done",
        "cleanup_complete": True,
    }
    report = _scenario_report([row], 1, "fixture")
    assert report["task_success_assessed"] is False
    assert report["recovery_denominator"] == 0
    assert report["recovery_success_rate"] is None
    assert report["recovery_successes"] is None


def test_live_agent_timeout_stays_in_recovery_denominator():
    from mini_agent.evaluation.reliability_report import _scenario_report

    row = {
        "fault_status": "triggered", "invariant_status": "passed",
        "recovery_status": "failed", "grader_passed": False,
        "source_consistent": True, "infrastructure_error": None,
        "agent_stop_reason": "agent_timeout", "agent_terminal_status": "failed",
        "cleanup_complete": True,
    }
    report = _scenario_report([row], 1, "live")
    assert report["recovery_denominator"] == 1
    assert report["recovery_successes"] == 0
    assert report["recovery_success_rate"] == 0


def test_report_counts_infrastructure_and_never_started_slots_separately():
    from mini_agent.evaluation.reliability_report import _scenario_report

    row = {
        "fault_status": "triggered", "invariant_status": "passed",
        "recovery_status": "failed", "grader_passed": False,
        "source_consistent": True, "infrastructure_error": None,
        "agent_stop_reason": "agent_error", "agent_terminal_status": "failed",
        "cleanup_complete": True,
    }
    report = _scenario_report(
        [row], 3, "live", infrastructure_error_slots=1, unrun_slots=1,
    )
    assert report["infrastructure_errors"] == 1
    assert report["unrun_slots"] == 1
    assert report["recovery_denominator_exclusions"]["infrastructure_error"] == 1
    assert report["recovery_denominator_exclusions"]["unrun_slots"] == 1


def test_reliability_report_checks_frozen_suite_and_slot_plan(tmp_path):
    from mini_agent.evaluation.reliability_report import build_reliability_report

    suite = load_suite(SUITE_PATH)
    root = tmp_path / "run"
    frozen = root / "frozen"
    frozen.mkdir(parents=True)
    shutil.copy2(SUITE_PATH, frozen / "suite.json")
    shutil.copytree(SUITE_PATH.parent / "support", frozen / "support")
    for entry in suite.scenario_entries:
        shutil.copytree(SUITE_PATH.parent / entry["path"], frozen / entry["path"])
    slots = reliability_plan(suite, run_kind="fixture")
    ledger = {
        "format": "mini_agent.reliability_run", "schema_version": 1,
        "suite_run_id": "00000000-0000-0000-0000-000000000001",
        "suite_id": suite.suite_id, "suite_version": suite.version,
        "suite_sha256": suite.suite_sha256, "run_kind": "fixture", "repeats": 2,
        "status": "interrupted", "slots": slots,
    }
    (root / "suite-run.json").write_text(json.dumps(ledger), encoding="utf-8")
    report = build_reliability_report(root)
    assert report["cohorts"]["fixture"]["planned_slots"] == 50
    assert report["cohorts"]["fixture"]["task_success_assessed"] is False
    tampered = dict(ledger, suite_sha256="0" * 64)
    (root / "suite-run.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="冻结 suite 不匹配"):
        build_reliability_report(root)


def test_infrastructure_trial_retains_fixture_or_live_run_kind():
    from mini_agent.evaluation.reliability import _empty_infra_result

    suite = load_suite(SUITE_PATH)
    scenario = suite.scenarios[0]
    slot = {"run_kind": "live", "repetition": 1, "variant": {}}
    result = _empty_infra_result(suite, scenario, slot, "trial", "provider_error")
    assert result["run_kind"] == "live"
    assert result["infrastructure_error"] == "provider_error"
