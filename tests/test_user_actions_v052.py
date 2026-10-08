from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent.context import ContextManager
from mini_agent.session import SessionStore
from mini_agent.state import AgentState, PlanRejected
from mini_agent.tools.base import ExecutionResult
from mini_agent.user_actions import (
    decide_plan, resolve_crash_issue, review_plan, save_safe_point,
)


def _crash_state():
    state = AgentState()
    state.begin_task("user action boundary")
    from mini_agent.state import canonical_arguments_hash
    arguments = {"path": "src/example.py"}
    state.begin_crash_recovery(
        "source-session", 1, 1, "a" * 64, 1,
        [{"invocation_id": "inv-1", "tool": "write_file", "effect_class": "possible",
          "handler_admitted": True, "attempt_id": "at-1",
          "arguments_hash": canonical_arguments_hash(arguments),
          "arguments_summary": arguments}],
    )
    return state


def test_crash_issue_requires_investigation_and_records_simulated_source():
    state = _crash_state()
    issue = state.unresolved_crash_issues[0]
    with pytest.raises(PlanRejected, match="investigate"):
        resolve_crash_issue(
            state, issue.issue_id, "continue", "premature", source="simulated_user",
            metadata={"script_sha256": "a" * 64, "trigger": "uninvestigated",
                      "issue_id": issue.issue_id, "accepted": False},
        )
    action = resolve_crash_issue(
        state, issue.issue_id, "investigate", "inspect first", source="simulated_user",
        metadata={"script_sha256": "a" * 64, "trigger": "uncertain_issue_found",
                  "issue_id": issue.issue_id, "accepted": True},
    )
    assert action.status == "accepted"
    assert action.source == "simulated_user"
    assert json.loads(action.detail)["script_sha256"] == "a" * 64
    attempt = state.record_execution_result(ExecutionResult(
        "read_file", {"path": "src/example.py"}, "allowed", True,
        "succeeded", 0, "none", "return value * 2", "observed",
    ))
    result = resolve_crash_issue(
        state, issue.issue_id, "continue", "continue after observation",
        source="simulated_user", metadata={"issue_id": issue.issue_id, "accepted": True},
    )
    assert result.status == "accepted"
    assert result.affected_ids == (issue.issue_id,)
    assert state.crash_decisions[-1].investigation_attempt_id == attempt.attempt_id


def test_block_decision_stops_recovery_and_plan_actions_are_revision_bound():
    state = _crash_state()
    issue = state.unresolved_crash_issues[0]
    action = resolve_crash_issue(state, issue.issue_id, "block", "stop", source="cli")
    assert action.status == "accepted"
    assert state.status == "blocked"

    plan_state = AgentState()
    plan_state.begin_task("plan-only action", mode="plan_only")
    plan_state.record_execution_result(ExecutionResult(
        "read_file", {"path": "src/example.py"}, "allowed", True,
        "succeeded", 0, "none", "return value * 2", "observed",
    ))
    revision = plan_state.commit_plan(
        "repair", [], ["verification passes"],
        [{"step_id": "inspect", "content": "inspect source", "depends_on": [],
          "success_criteria": ["source understood"], "replaces": []}], "initial plan",
    )
    with pytest.raises(PlanRejected, match="revision"):
        decide_plan(plan_state, "approved", revision.revision_id + 1)
    approval = decide_plan(
        plan_state, "approved", revision.revision_id, source="simulated_user",
        metadata={"script_sha256": "b" * 64, "revision_id": revision.revision_id,
                  "accepted": True},
    )
    assert approval.record["revision_id"] == revision.revision_id


def test_shared_safe_save_helper_saves_only_a_safe_point(tmp_path):
    state = AgentState()
    state.begin_task("persist safe task")
    context = ContextManager(state, [{"role": "user", "content": "persist"}], observability=False)
    store = SessionStore(tmp_path / "sessions")
    action = save_safe_point(
        state=state, context=context, store=store, session_id=None,
        workspace_root=str(tmp_path),
    )
    assert action.status == "accepted"
    assert action.envelope["save_kind"] == "safe_point"
    assert store.load(action.session_id)["state"]["task"] == "persist safe task"

    unsafe = SimpleNamespace(task="unsafe while child active", active_delegation_records=[SimpleNamespace(
        mode="background", subagent_id="child-active",
    )])
    rejected = save_safe_point(
        state=unsafe, context=object(),
        store=store, session_id=None, workspace_root=str(tmp_path),
    )
    assert rejected.status == "rejected"
    assert rejected.affected_ids == ("child-active",)
