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
    return _offline_probe(scenario, workspace, scenario_dir, FaultController(scenario["fault"]))


def test_process_wait_timeout_requires_stop_sync_and_cleanup(tmp_path):
    result = _probe(tmp_path, "process-wait-timeout")
    assert result["fault_status"] == "triggered"
    assert result["invariant_status"] == "passed", result
    assert result["cleanup_complete"] is True


@pytest.mark.parametrize("scenario_id", [
    "mcp-disconnect", "mcp-remote-error", "mcp-is-error", "mcp-call-timeout",
])
@pytest.mark.parametrize("transport", ["stdio", "loopback_http"])
def test_mcp_transport_variants_preserve_protocol_and_cleanup_boundaries(tmp_path, scenario_id, transport):
    result = _probe(tmp_path, scenario_id, transport=transport)
    assert result["fault_status"] == "triggered", result
    assert result["invariant_status"] == "passed", result
    assert result["cleanup_complete"] is True


@pytest.mark.parametrize("scenario_id", [
    "subagent-timeout", "subagent-cancel", "subagent-unclaimed",
    "subagent-interrupted", "followup-incompatible",
])
def test_subagent_faults_use_real_manager_and_claimed_lifecycle(tmp_path, scenario_id):
    result = _probe(tmp_path, scenario_id)
    assert result["fault_status"] == "triggered", result
    assert result["invariant_status"] == "passed", result
    assert result["cleanup_complete"] is True
    if scenario_id == "subagent-timeout":
        assert result["state_summary"]["injected_calls"] == [{
            "component": "subagent_llm", "source": "injected",
            "counted_as_real": False, "count": 1,
        }]
