"""Offline regressions for the observed September 30 live failures."""

from copy import deepcopy
import json

import pytest

from mini_agent.context import ContextManager
from mini_agent.evaluation.reliability_schema import load_suite
from mini_agent.evaluation.reliability_worker import _reliability_protected_messages
from mini_agent.recovery import RecoveryRuntime, make_recover_tool
from mini_agent.session import DurableToolBoundary, SessionStore
from mini_agent.state import AgentState, SessionExportError
from mini_agent.permission import PermissionGate, PermissionPolicy
from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry
from test_task_budget_v052 import _runtime, _late_recovery_runtime


@pytest.mark.parametrize("failure_id", ["", "f-missing", None, [], {}])
def test_invalid_recovery_reference_commits_durable_rejection(tmp_path, failure_id):
    reply = {"role": "assistant", "tool_calls": [{
        "id": "bad-recovery", "type": "function", "function": {
            "name": "recover", "arguments": json.dumps({
                "action": "adjust", "caused_by_failure_id": failure_id,
                "reason": "repair", "requested_tool": "observe", "requested_arguments": {},
            }),
        },
    }]}
    runtime, state, context = _runtime(tmp_path, lambda *_args, **_kw: reply, limit=None)
    # One real tool failure enters diagnosis; the invalid recover must not
    # invent a causal reference, advance generation, or enter a target handler.
    registry = ToolRegistry()
    registry.register(Tool("fail", "failure", {"type": "object", "properties": {}},
                           lambda: (_ for _ in ()).throw(FileNotFoundError()), effect_class="none"))
    failed = ToolExecutor(registry, PermissionGate(PermissionPolicy({"fail": "allow"})))
    state.record_execution_result(failed.execute_result("fail", {}, state))
    runtime.executor.registry.register(make_recover_tool(RecoveryRuntime(state, runtime.executor)))
    runtime.executor.gate = PermissionGate(PermissionPolicy({"recover": "allow", "observe": "allow"}))
    store = SessionStore(tmp_path / "sessions")
    sid = store.save(None, state, context, workspace_root=tmp_path)["session_id"]
    runtime.session_boundary = DurableToolBoundary(store, sid, tmp_path)
    runtime.executor.session_boundary = runtime.session_boundary
    runtime.max_rounds = 1
    generation = state.current_generation_id
    runtime.run()
    saved = store.load(sid)
    assert saved["tool_boundary"]["status"] == "committed"
    assert saved["state"]["recovery_actions"][-1]["caused_by_failure_id"] is None
    assert saved["state"]["recovery_actions"][-1]["status"] == "rejected"
    assert state.current_generation_id == generation
    assert len(state.failures) == 1 and len(state.attempts) == 1
    result = json.loads(context.history[-1]["content"])
    assert result["active_failure_id"] == "f-1"
    assert result["repair_phase"] == "diagnosis_required"
    restored = AgentState.restore_session(saved["state"], allow_pending=True)
    assert restored.recovery_actions[-1].caused_by_failure_id is None
    tampered = deepcopy(saved["state"])
    tampered["recovery_actions"][-1]["status"] = "executed"
    with pytest.raises(SessionExportError, match="recovery action"):
        AgentState.validate_session_export(tampered, allow_pending=True)
    tampered = deepcopy(saved["state"])
    del tampered["recovery_actions"][-1]["caused_by_failure_id"]
    with pytest.raises(SessionExportError, match="recovery action"):
        AgentState.validate_session_export(tampered, allow_pending=True)


def test_rejection_without_any_failure_can_be_saved():
    state = AgentState()
    state.begin_task("inspect")
    state.reject_recovery("adjust", "f-missing", "invalid", "未知 failure")
    exported = state.export_session()
    assert exported["recovery_actions"][0]["caused_by_failure_id"] is None
    assert not exported["failures"]


@pytest.mark.parametrize("remaining,plan", [(19520, False), (27000, True)])
def test_full_parent_prompt_late_verification_and_final_reply(tmp_path, remaining, plan):
    runtime, state, context, turns = _late_recovery_runtime(tmp_path, remaining, plan=plan)
    context.protected_messages = _reliability_protected_messages(tmp_path)
    context.finalization_protected_messages = _reliability_protected_messages(tmp_path, finalization=True)
    original = deepcopy(context.history)
    original_protected = deepcopy(context.protected_messages)
    compact_closing = runtime._closing_reservation()
    context.finalization_protected_messages = None
    previous_closing = runtime._closing_reservation()
    context.finalization_protected_messages = _reliability_protected_messages(tmp_path, finalization=True)
    assert compact_closing < previous_closing / 2
    result = runtime.run()
    assert result.stop_reason == "text", (result, runtime.budget_proposal)
    assert state.has_verification_evidence() and not state.completion_blockers()
    assert turns[-1]["include_tools"] is False
    assert len(turns) == (3 if plan else 2)
    assert context.history[:len(original)] == original
    assert context.protected_messages == original_protected
    assert runtime.task_budget.used <= 64000
    assert runtime.task_budget.data["overrun_tokens"] == 0


def test_final_view_preserves_constraints_without_old_tool_rounds(tmp_path):
    from mini_agent.prompt import build_system_prompt, build_completion_prompt
    state = AgentState()
    state.begin_task("repair")
    project = "用户约束：保留指定输出格式 PRIVATE_CONSTRAINT"
    protected = [{"role": "system", "content": build_system_prompt(project_instructions=project)}]
    context = ContextManager(state, [
        {"role": "user", "content": "repair"},
        {"role": "assistant", "tool_calls": [{"id": "old", "type": "function", "function": {
            "name": "read_file", "arguments": "{}"}}], "reasoning_content": "old reasoning"},
        {"role": "tool", "tool_call_id": "old", "content": "old tool result"},
        {"role": "user", "content": "late user correction"},
        {"role": "system", "content": "additional protected constraint"},
    ], protected_messages=protected, finalization_protected_messages=[{
        "role": "system", "content": build_completion_prompt(project_instructions=project)}],
        observability=False, memory_retrieval_enabled=False)
    original = deepcopy(context.history)
    view = context._task_budget_view(100000, True, observe=False)
    text = json.dumps(view, ensure_ascii=False)
    assert project in text and "late user correction" in text
    # System constraints in history must not disappear with old tool rounds.
    assert "additional protected constraint" in text
    assert "old reasoning" not in text and "old tool result" not in text
    assert context.history == original and context.protected_messages == protected


def test_suite_exposes_workspace_discovery():
    from pathlib import Path
    root = Path(__file__).parent / "fixtures/evaluation/reliability"
    suite = load_suite(root / "suite.json")
    scenario = next(s for s in suite.scenarios if s.scenario_id == "verification-repair")
    assert {"list_dir", "grep"} <= set(scenario.allowed_tools)
    for name in ("list_dir", "grep"):
        assert any(r["tool"] == name and r["action"] == "allow" for r in scenario.permission_rules)
