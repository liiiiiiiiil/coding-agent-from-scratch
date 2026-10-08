from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from mini_agent.evaluation.faults import FaultController
from mini_agent.evaluation.reliability_schema import load_suite
from mini_agent.evaluation.reliability_worker import _offline_probe


RELIABILITY = Path(__file__).parent / "fixtures" / "evaluation" / "reliability"


def _probe(tmp_path: Path, scenario_id: str, **variant):
    suite = load_suite(RELIABILITY / "suite.json")
    scenario = next(item for item in suite.scenarios if item.scenario_id == scenario_id).to_dict()
    scenario["fault"]["parameters"].update(variant)
    scenario_dir = RELIABILITY / scenario_id
    workspace = tmp_path / "workspace"
    shutil.copytree(scenario_dir / "initial", workspace)
    controller = FaultController(scenario["fault"])
    return _offline_probe(scenario, workspace, scenario_dir, controller)


@pytest.mark.parametrize("scenario_id", [
    "crash-before-admission", "crash-after-admission", "crash-after-handler",
])
def test_real_new_process_crash_recovery_preserves_source_and_never_replays(tmp_path, scenario_id):
    result = _probe(tmp_path, scenario_id)
    assert result["fault_status"] == "triggered"
    assert result["invariant_status"] == "passed", result
    assert result["recovery_status"] in {"succeeded", "incomplete"}
    assert result["state_summary"]["source_session_unchanged"] is True


@pytest.mark.parametrize("boundary", ["admission", "per_call", "round_commit"])
def test_each_durable_commit_failure_stops_at_the_failed_boundary(tmp_path, boundary):
    result = _probe(tmp_path, "durable-commit-failure", boundary=boundary)
    assert result["fault_status"] == "triggered"
    assert result["invariant_status"] == "passed", result
    details = result["state_summary"]
    assert details["boundary"] == boundary
    assert details["model_calls"] == 1
    assert details["handler_calls"] == {"admission": 0, "per_call": 1, "round_commit": 2}[boundary]


@pytest.mark.parametrize("decision_path", ["continue", "block"])
def test_recovery_feedback_is_user_sourced_and_block_does_not_resume(tmp_path, decision_path):
    result = _probe(tmp_path, "recovery-user-decisions", decision_path=decision_path)
    assert result["fault_status"] == "triggered"
    assert result["invariant_status"] == "passed", result
    assert result["state_summary"]["recovery_invariants"]["invalid-user-feedback-rejected"] is True
    assert result["state_summary"]["recovery_invariants"]["single-source-claim"] is True
    events = result["feedback_events"]
    assert events and all(event["source"] == "simulated_user" for event in events)
    if decision_path == "block":
        assert result["recovery_status"] == "succeeded"
        assert result["state_summary"]["recovery_invariants"]["block-stops-task"] is True
    else:
        assert result["recovery_status"] == "incomplete"
