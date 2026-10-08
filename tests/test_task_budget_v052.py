"""Task budget, durable request boundaries, context views and true completion."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent.agent import ParentRuntimePolicy
from mini_agent.budget import (TaskBudgetController, BudgetPersistenceError,
                               BudgetUnavailable, request_key, validate_snapshot)
from mini_agent.context import ContextManager, count_tokens
from mini_agent.permission import PermissionGate, PermissionPolicy
from mini_agent.providers.base import ProviderUsage, UsageMeter, ProviderResponse
from mini_agent.runtime import AgentRuntime
from mini_agent.session import SessionStore, DurableToolBoundary, SessionValidationError
from mini_agent.state import AgentState
from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry

KEY = request_key({"fingerprint": "0" * 64}, [], "work")


def test_calibration_cold_warm_latest_eight_and_idempotent_restore():
    controller = TaskBudgetController(64000)
    for raw, actual in [(1000, 1500), (2000, 2700)]:
        assert controller.input_reservation(raw, KEY) == raw * 2 + 256
        rid = controller.reserve(controller.proposal(raw, KEY))
        controller.settle(rid, actual, 100, provider=True)
        assert not controller.settle(rid, actual, 100, provider=True)
    assert controller.input_reservation(1000, KEY) == 2126  # ceil((1000+700)*1.1)+256
    restored = TaskBudgetController(1, snapshot=controller.snapshot())
    assert restored.used == 4400 and restored.data["limit"] == 64000
    assert restored.input_reservation(1000, KEY) == 2126
    for _ in range(8):
        rid = restored.reserve(restored.proposal(1000, KEY))
        restored.settle(rid, 1100, 100, provider=True)
    assert restored.input_reservation(1000, KEY) == 1466
    assert len(restored.data["samples"][KEY]) == 8
    new_key = request_key({"fingerprint": "1" * 64}, [], "work")
    assert restored.input_reservation(1000, new_key) == 2256
    assert request_key(None, [], "work") != request_key(None, [], "final")
    assert request_key(None, [{}], "work") != request_key(None, [], "work")


def test_unknown_request_charges_full_reservation_once_after_restart():
    controller = TaskBudgetController(64000)
    rid = controller.reserve(controller.proposal(1000, KEY))
    snapshot = controller.snapshot()
    restored = TaskBudgetController(snapshot=snapshot)
    assert restored.reconcile_pending()
    assert restored.used == 2256 + 1024
    assert not restored.reconcile_pending()
    assert not restored.settle(rid, 1, 1, provider=True)
    assert restored.data["unknown_requests"] == 1
    assert restored.data["samples"][KEY] == []


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5])
def test_invalid_limit_rejected(bad):
    with pytest.raises(ValueError):
        TaskBudgetController(bad)


def _runtime(tmp_path, client, *, limit=64000, boundary=None, state=None, binding=None):
    state = state or AgentState()
    if not state.task:
        state.begin_task("repair src/example.py and verify independently")
    context = ContextManager(state, [{"role": "user", "content": state.task}],
                             protected_messages=[{"role": "system", "content": "test constraints"}],
                             model_binding=binding, memory_retrieval_enabled=False, observability=False)
    registry = ToolRegistry()
    registry.register(Tool("observe", "read-only observation", {"type": "object", "properties": {}},
                           lambda: "observed", effect_class="none"))
    executor = ToolExecutor(registry, PermissionGate(PermissionPolicy({"observe": "allow"})),
                            on_result=state.record_tool)
    runtime = AgentRuntime(llm_client=client, context=context, executor=executor,
                           policy=ParentRuntimePolicy(), max_rounds=8,
                           token_budget=limit, session_boundary=boundary, model_binding=binding)
    return runtime, state, context


def test_ordinary_cli_budget_disabled_and_legacy_resume_not_reset(tmp_path):
    runtime, state, _ = _runtime(tmp_path, lambda *_: {"role": "assistant", "content": "done"}, limit=None)
    assert runtime.task_budget is None
    restored = AgentState.restore_session(state.export_session(), str(tmp_path))
    old, _, _ = _runtime(tmp_path, lambda *_: {}, state=restored)
    assert old.task_budget is None
    restored.begin_task("new task")
    new, _, _ = _runtime(tmp_path, lambda *_: {}, state=restored)
    assert new.task_budget.data["limit"] == 64000


def test_request_reservation_commit_failure_prevents_llm(tmp_path):
    calls = []
    boundary = SimpleNamespace(persist_request_budget=lambda *_: (_ for _ in ()).throw(OSError("failed")))
    runtime, _, _ = _runtime(tmp_path, lambda *_: calls.append(True), boundary=boundary)
    with pytest.raises(BudgetPersistenceError):
        runtime.run()
    assert calls == []


def test_response_settlement_commit_failure_prevents_tools(tmp_path):
    commits = []
    meter = UsageMeter()
    binding = SimpleNamespace(usage_meter=meter, reference={"fingerprint": "0" * 64})
    def persist(*_):
        commits.append(True)
        if len(commits) == 2:
            raise OSError("settlement failed")
    def client(messages, **options):
        meter.record(ProviderUsage(count_tokens(messages), 100, "provider"))
        return {"role": "assistant", "tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "observe", "arguments": "{}"}}]}
    runtime, state, context = _runtime(tmp_path, client, binding=binding,
        boundary=SimpleNamespace(persist_request_budget=persist))
    with pytest.raises(BudgetPersistenceError):
        runtime.run()
    assert not state.attempts and len(context.history) == 1


def test_pending_request_durable_ledger_safe_point_and_unknown_restore(tmp_path):
    runtime, state, context = _runtime(tmp_path, lambda *_: {})
    store = SessionStore(tmp_path / "sessions")
    sid = store.save(None, state, context, workspace_root=tmp_path)["session_id"]
    boundary = DurableToolBoundary(store, sid, tmp_path)
    runtime.session_boundary = boundary
    rid = runtime.task_budget.reserve(runtime.task_budget.proposal(1000, KEY))
    runtime._persist_budget()
    envelope = store.load(sid)
    assert envelope["state"]["task_budget"]["pending"]["request_id"] == rid
    with pytest.raises(SessionValidationError, match="LLM"):
        store.save(sid, state, context, workspace_root=tmp_path)
    restored = AgentState.restore_session(envelope["state"], str(tmp_path), allow_pending=True)
    successor, _, _ = _runtime(tmp_path, lambda *_: {}, state=restored, limit=999999)
    assert successor.task_budget.data["limit"] == 64000
    assert successor.task_budget.reconcile_pending()
    assert successor.task_budget.used == 3280
    malformed = deepcopy(envelope["state"])
    malformed["task_budget"]["pending"]["request_id"] = 3
    with pytest.raises(ValueError):
        validate_snapshot(malformed["task_budget"])


def test_context_keeps_reasoning_and_complete_pairs_without_mutating_history(tmp_path):
    _, state, context = _runtime(tmp_path, lambda *_: {})
    for i in range(6):
        context.history.extend([
            {"role": "assistant", "reasoning_content": "R" * 600,
             "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "observe", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": str(i), "content": "T" * 1200},
        ])
    context.history.insert(3, {"role": "user", "content": "preserve this correction"})
    original = deepcopy(context.history)
    view = context.prepare_messages(input_allowance=1200)
    assert count_tokens(view) <= 1200
    assert any(m.get("content") == "preserve this correction" for m in view)
    calls = [c["id"] for m in view for c in m.get("tool_calls", [])]
    assert calls == [m["tool_call_id"] for m in view if m["role"] == "tool"]
    assert all(m["reasoning_content"] == "R" * 600 for m in view if m.get("tool_calls"))
    assert context.history == original
    with pytest.raises(BudgetUnavailable):
        context.prepare_messages(input_allowance=1)


@pytest.mark.parametrize("tools", [False, True])
def test_provider_overrun_records_actual_and_closes_all_tool_calls(tmp_path, tools):
    meter = UsageMeter()
    binding = SimpleNamespace(usage_meter=meter, reference={"fingerprint": "0" * 64})
    def client(messages, **options):
        meter.record(ProviderUsage(65000, 2000, "provider"))
        return {"role": "assistant", "content": "done", **({"tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "observe", "arguments": "{}"}},
            {"id": "two", "type": "function", "function": {"name": "observe", "arguments": "{}"}},
        ]} if tools else {})}
    runtime, state, context = _runtime(tmp_path, client, binding=binding)
    result = runtime.run()
    assert result.stop_reason == "token_limit" and state.status == "failed"
    assert runtime.task_budget.used == 67000
    assert runtime.task_budget.data["overrun_tokens"] > 0
    assert not any(a.handler_admitted for a in state.attempts)
    assert len([m for m in context.history if m["role"] == "tool"]) == (2 if tools else 0)


def test_finalization_requires_actual_evidence_and_model_text(tmp_path):
    meter = UsageMeter()
    binding = SimpleNamespace(usage_meter=meter, reference={"fingerprint": "0" * 64})
    seen = []
    def client(messages, **options):
        seen.append(options)
        meter.record(ProviderUsage(count_tokens(messages) + 100, 100, "provider"))
        return {"role": "assistant", "content": "verified done"}
    runtime, state, _ = _runtime(tmp_path, client, binding=binding)
    assert not state.finalization_ready()
    state._last_verified_generation = state._verification_generation
    assert state.finalization_ready()
    assert runtime.run().stop_reason == "text"
    assert seen[0]["include_tools"] is False
    assert seen[0]["max_output_tokens"] == 512
    assert state.status == "running"  # CLI owns the final terminal transition.


def test_finalization_hallucinated_tool_is_returned_and_blocked(tmp_path):
    def client(messages, **options):
        return {"role": "assistant", "tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "observe", "arguments": "{}"}}]}
    runtime, state, context = _runtime(tmp_path, client)
    state._last_verified_generation = state._verification_generation
    result = runtime.run()
    assert result.stop_reason == "blocked" and state.status == "blocked"
    assert context.history[-1]["role"] == "tool"
    assert not state.attempts[-1].handler_admitted


def test_summary_shares_budget_caps_and_unknown_usage(tmp_path):
    meter = UsageMeter()
    seen = []
    def complete(messages, **options):
        seen.append(options)
        meter.record(ProviderUsage(count_tokens(messages) + 50, 100, "provider"))
        return ProviderResponse({"role": "assistant", "content": "bounded summary"}, "stop",
                                ProviderUsage(count_tokens(messages) + 50, 100, "provider"))
    binding = SimpleNamespace(usage_meter=meter, reference={"fingerprint": "0" * 64}, complete=complete)
    runtime, state, context = _runtime(tmp_path, lambda *_: {}, binding=binding)
    for i in range(6):
        context.history.extend([{"role": "assistant", "content": "OLD" * 4000},
                                {"role": "tool", "content": "", "tool_call_id": str(i)}])
    original = deepcopy(context.history)
    assert runtime._budgeted_summary([{"role": "user", "content": "summarize a small bounded excerpt"}]) == "bounded summary"
    assert seen[0]["max_output_tokens"] == 512 and not seen[0]["include_tools"]
    assert state.task_budget["last_settled_id"] == 1
    assert runtime.task_budget.used > 100
    assert context.history == original
    runtime.task_budget.data["input_tokens"] = 63999
    with pytest.raises(BudgetUnavailable):
        runtime._budgeted_summary([{"role": "user", "content": "small"}])
    assert len(seen) == 1


def test_summary_commit_failure_is_not_swallowed_by_compaction(tmp_path):
    runtime, _, context = _runtime(tmp_path, lambda *_: {})
    context.history.extend([{"role": "assistant", "content": "old"}] * 6)
    context.summarizer = lambda *_args, **_options: (_ for _ in ()).throw(BudgetPersistenceError("commit"))
    with pytest.raises(BudgetPersistenceError):
        context.compact(keep_rounds=2)


@pytest.mark.parametrize("scenario_id", ["tool-handler-exception", "verification-repair"])
def test_complete_recovery_costs_include_verification_and_finalization(tmp_path, monkeypatch, scenario_id):
    from test_evaluation_worker_v052 import _worker, _response
    meter = UsageMeter()
    binding = SimpleNamespace(
        profile=SimpleNamespace(context_window=128000, max_output_tokens=8192, model_id="fixture"),
        provider=SimpleNamespace(api_key="fixture-key", endpoint="https://fixture.invalid", extra_headers={}),
        reference=SimpleNamespace(to_dict=lambda: {"profile": "fixture", "provider": "fixture",
            "protocol": "openai_chat", "fingerprint": "0" * 64}), usage_meter=meter)
    turns = []
    def client(messages, **options):
        turns.append(options)
        raw = count_tokens(messages) + (count_tokens(options["tool_registry"].schemas()) if options["include_tools"] else 0)
        output = 900 if options["include_tools"] else 200
        meter.record(ProviderUsage(int(raw * 1.25), output, "provider"))
        turn = len(turns)
        if turn == 1:
            message = _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "initial") if scenario_id == "verification-repair" else _response("read_file", {"path": "src/example.py"}, "initial")
        elif turn == 2:
            message = _response("read_file", {"path": "src/example.py"}, "observe")
        elif turn == 3:
            message = _response("recover", {"action": "adjust", "caused_by_failure_id": "f-1",
                "reason": "fix observed implementation", "requested_tool": "write_file",
                "requested_arguments": {"path": "src/example.py", "content": "def transform(value):\n    return value * 2\n"}}, "repair")
        elif turn == 4:
            message = _response("run_shell", {"command": "python -m unittest discover -s tests", "purpose": "verification"}, "verify")
        else:
            message = {"role": "assistant", "content": "修复已独立验证"}
        message["reasoning_content"] = "reasoning " * (250 if turn < 5 else 50)
        return message
    result = _worker(tmp_path, monkeypatch, scenario_id, client, binding=binding)
    assert result["recovery_status"] == "succeeded", result
    evidence = result["state_summary"]["completion_evidence"]
    assert all(evidence[k] for k in ("artifact_correct", "current_generation_verified", "model_completed"))
    assert evidence["blockers"] == []
    diagnostic = result["phases"]["agent"]["request_diagnostics"]
    assert len(turns) == 5 and turns[-1]["max_output_tokens"] == 512
    assert turns[-1]["include_tools"] is False
    assert diagnostic["cumulative_tokens_after"] == meter.snapshot()["input_tokens"] + meter.snapshot()["output_tokens"]
    assert diagnostic["cumulative_tokens_after"] < 64000
    assert diagnostic["requests"][-1]["tool_schema_tokens_estimated"] == 0
    assert diagnostic["requests"][-1]["request_kind"] == "final"
    assert all(row["output_tokens"] <= row["max_output_tokens"] for row in diagnostic["requests"])


def test_archived_cost_replay_only_uses_observed_numeric_facts():
    from mini_agent.evaluation.cost_replay import replay_request_costs
    root = Path(__file__).parents[1] / "docs/evaluation/baselines/v0.52/live-20260929-06"
    original = {p: p.read_bytes() for p in root.rglob("*.json")}
    replay = replay_request_costs(root)
    assert replay["totals"]["decisions"] == 144
    assert replay["totals"]["historical_refusals"] == 16
    assert replay["totals"]["input_tokens"] == 885685
    assert replay["totals"]["output_tokens"] == 44417
    assert "recovery_success" not in replay["totals"]
    assert "No future model replies" in replay["limitations"]
    assert all(path.read_bytes() == data for path, data in original.items())


@pytest.mark.parametrize("blocker", ["verification", "planning", "processes", "delegations", "crash_issues", "repair", "pending_attempts", "plan_steps"])
def test_completion_blockers_prevent_finalization_without_mutating_facts(blocker):
    state = AgentState()
    state.begin_task("task")
    state._last_verified_generation = state._verification_generation
    if blocker == "verification":
        state._verification_required = True
    elif blocker == "planning":
        from dataclasses import replace
        state.planning_state = replace(state.planning_state, phase="awaiting_approval")
    elif blocker == "processes":
        state.process_records.append(SimpleNamespace(status="running", write_pending=False))
    elif blocker == "delegations":
        state.delegation_records.append(SimpleNamespace(delivery_status="result_ready"))
    elif blocker == "crash_issues":
        state.crash_issues.append(SimpleNamespace(status="unresolved"))
    elif blocker == "repair":
        state._repair_phase = "diagnosis_required"
    elif blocker == "pending_attempts":
        state._pending_attempts.add("a-1")
    else:
        from test_loop import _start_plan
        _start_plan(state)
    first = state.completion_blockers()
    assert blocker in first and not state.finalization_ready()
    assert state.completion_blockers() == first and state.status == "running"


def test_budget_views_preserve_skill_directory_and_fresh_memory_queries(tmp_path):
    _, _, context = _runtime(tmp_path, lambda *_: {})
    context.skill_catalog = SimpleNamespace(directory_prompt=lambda *_args, **_kwargs: "allowed_skill: bounded description")
    retrievals = []
    context._retrieve_memory_candidates = lambda: retrievals.append(True) or None
    for _ in range(2):
        view = context.prepare_messages(input_allowance=1500)
        assert any(m.get("name") == "skill_catalog" for m in view)
        assert count_tokens(view) <= 1500
    assert len(retrievals) == 2
    context._task_budget_view(1500, False, observe=False)
    assert len(retrievals) == 2  # Summary cost checks do not change retrieval state.


def test_saved_controller_pending_submission_integrity_constraints(tmp_path):
    controller = TaskBudgetController(64000)
    proposal = controller.proposal(1000, KEY)
    assert proposal["output"] == 1024
    controller.reserve(proposal)
    snapshot = controller.snapshot()
    assert snapshot["pending"]["input"] + snapshot["pending"]["output"] <= snapshot["limit"]
    validate_snapshot(snapshot)


def test_cli_explicit_opt_in_refuses_before_provider_io(tmp_path, monkeypatch):
    from mini_agent.agent import agent_loop
    runtime, state, context = _runtime(tmp_path, lambda *_: {}, limit=None)
    monkeypatch.setattr("mini_agent.config.PARENT_TASK_TOKEN_BUDGET", 256)
    monkeypatch.setattr("mini_agent.agent.call_llm", lambda *_args, **_options: pytest.fail("must not send"))
    assert "protected_context" in agent_loop(context, runtime.executor)
    assert state.status == "failed" and state.task_budget["limit"] == 256


def test_all_archived_live_reports_rebuild_byte_identically():
    from mini_agent.evaluation.reliability_report import build_reliability_report
    baseline = Path(__file__).parents[1] / "docs/evaluation/baselines/v0.52"
    checked = 0
    for root in sorted(baseline.glob("live-*")):
        archived = root / "report.json"
        if not archived.is_file():
            continue
        original = archived.read_bytes()
        rebuilt = (json.dumps(build_reliability_report(root), ensure_ascii=False, indent=2) + "\n").encode()
        assert rebuilt == original, root.name
        assert archived.read_bytes() == original
        checked += 1
    assert checked >= 4


def _late_recovery_runtime(tmp_path, remaining, *, plan=False):
    """Live-shaped fixed input costs, long history, and warm provider error."""
    meter = UsageMeter()
    binding = SimpleNamespace(usage_meter=meter, reference={"fingerprint": "0" * 64})
    turns = []
    def client(messages, **options):
        turns.append(options)
        schemas = runtime.executor.registry.schemas() if options["include_tools"] else []
        raw = count_tokens(messages) + count_tokens(schemas)
        meter.record(ProviderUsage(raw + (3000 if schemas else 1500), 150, "provider"))
        if state.unfinished_todos():
            return {"role": "assistant", "tool_calls": [{"id": "progress", "type": "function",
                "function": {"name": "update_plan_progress", "arguments": json.dumps({
                    "revision_id": 1, "step_id": "inspect", "status": "completed", "reason": "observed"})}}]}
        if options["include_tools"]:
            return {"role": "assistant", "tool_calls": [{"id": "verify", "type": "function",
                "function": {"name": "run_shell", "arguments": json.dumps({
                    "command": "fixture verification", "purpose": "verification"})}}]}
        return {"role": "assistant", "content": "verified completion"}
    runtime, state, context = _runtime(tmp_path, client, binding=binding)
    context.protected_messages = [{"role": "system", "content": "fixed instructions " * 315}]
    for i in range(12):
        state.record_tool("read_file", {"path": f"src/file{i}.py"}, True, "observed fixed input")
        context.history.extend([
            {"role": "assistant", "tool_calls": [{"id": str(i), "type": "function",
                "function": {"name": "observe", "arguments": "{}"}}], "reasoning_content": "R" * 600},
            {"role": "tool", "tool_call_id": str(i), "content": "investigation " * 400},
        ])
    state.record_tool("edit_file", {"path": "src/example.py"}, True, "fixed artifact")
    registry = runtime.executor.registry
    registry.register(Tool("run_shell", "verification schema " * 130, {"type": "object",
        "properties": {"command": {"type": "string"}, "purpose": {"type": "string"}}},
        lambda **_: "[exit=0] independent fixture check passed", effect_class="possible"))
    runtime.executor.gate = PermissionGate(PermissionPolicy({"run_shell": "allow", "observe": "allow", "update_plan_progress": "allow"}))
    if plan:
        from test_loop import _start_plan
        _start_plan(state)
        from mini_agent.tools.plan import make_update_plan_progress_tool
        registry.register(make_update_plan_progress_tool(state))
    controller = runtime.task_budget
    controller.data["input_tokens"] = 64000 - remaining
    runtime._budget_start = controller.snapshot()
    key = runtime._budget_key(registry.schemas(), "work")
    controller.data["samples"][key] = [[4000, 7000], [4200, 7200]]
    return runtime, state, context, turns


@pytest.mark.parametrize("remaining,plan", [(19000, False), (27000, True)])
def test_late_recovery_can_spend_verification_and_plan_reserves(tmp_path, remaining, plan):
    runtime, state, context, turns = _late_recovery_runtime(tmp_path, remaining, plan=plan)
    original = deepcopy(context.history)
    runtime._prepare_budget_request()
    proposal = runtime.budget_proposal
    assert proposal["admitted"], proposal
    assert proposal["budget_stage"] == "verification_pending"
    assert proposal["raw_input"] > 3000
    assert proposal["protected_floor_tokens_estimated"] > 2000
    # The same fixed input was rejected by the previous double reservation,
    # before any provider dispatch. Compare policy arithmetic, not model luck.
    blockers = state.completion_blockers()
    old_rounds = 1 + ("verification" in blockers) + 2 * ("plan_steps" in blockers)
    protected = count_tokens(context.protected_messages) + count_tokens([
        m for m in context.history if m.get("role") == "user"])
    old_closing = old_rounds * (2 * (protected + 1024) + 256 + 512)
    old_allowance = min(runtime.task_budget.input_allowance(proposal["key"], 256, old_closing)
                        - count_tokens(runtime.executor.registry.schemas()), remaining // (old_rounds + 2))
    assert old_allowance < proposal["protected_floor_tokens_estimated"]
    assert context.history == original
    result = runtime.run()
    assert result.stop_reason == "text", (result, runtime.budget_proposal)
    assert state.has_verification_evidence() and not state.completion_blockers()
    assert turns[-1]["include_tools"] is False
    assert len(turns) == (3 if plan else 2)
    assert runtime.task_budget.used <= 64000
    assert runtime.task_budget.data["overrun_tokens"] == 0


def test_late_recovery_refusal_preserves_floor_and_reserves_without_io(tmp_path):
    from mini_agent.evaluation.reliability_diagnostics import RequestDiagnostics
    runtime, state, context, turns = _late_recovery_runtime(tmp_path, 11000)
    original = deepcopy(context.history)
    runtime._prepare_budget_request()
    proposal = runtime.budget_proposal
    assert not proposal["admitted"]
    assert proposal["reason"] == "protected_context_exceeds_task_budget"
    assert proposal["raw_input"] > 3000
    assert proposal["protected_floor_tokens_estimated"] > proposal["input_allowance_tokens_estimated"]
    diagnostic = RequestDiagnostics(runtime.model_binding, 64000)
    assert not diagnostic.before_request(runtime, 0)
    row = diagnostic.requests[-1]
    assert row["closing_reservation_tokens"] > 0
    assert row["reserved_input_tokens_estimated"] == proposal["raw_input"]
    assert row["completion_blockers"] == list(state.completion_blockers())
    assert runtime.run().stop_reason == "token_limit"
    assert turns == [] and context.history == original
    assert not state.has_verification_evidence()


def test_final_reservation_floor_preserves_late_user_corrections(tmp_path):
    runtime, _, context = _runtime(tmp_path, lambda *_: {})
    context.history.extend([{"role": "assistant", "content": "investigation"},
                            {"role": "user", "content": "late binding correction"}])
    view = context._task_budget_view(10000, True, observe=False, recent_rounds=0)
    assert any(m.get("content") == "late binding correction" for m in view)


def test_chat_estimate_covers_serialized_request_shape_without_retaining_text():
    from mini_agent.budget import request_input_estimate
    from mini_agent.providers.openai_chat import OpenAIChatAdapter
    model = SimpleNamespace(model_id="fixture-model", max_output_tokens=1024,
                            supports_streaming=False)
    provider = SimpleNamespace(endpoint="https://fixture.invalid/chat", api_key="secret",
                               timeout_seconds=1, extra_headers={})
    adapter = OpenAIChatAdapter(provider, model)
    messages = [{"role": "system", "content": "受保护 instruction " * 50},
                {"role": "user", "content": "repair"}]
    schemas = [{"type": "function", "function": {"name": "read_file", "description": "read",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    _, body, _ = adapter._request(messages, include_tools=True, tool_registry=schemas,
                                  stream=False, timeout=None, max_output_tokens=1024)
    estimate = request_input_estimate(messages, schemas, model_id=model.model_id,
                                      stream=False, max_output_tokens=1024)
    assert estimate == (len(body) * 11 + 29) // 30
    assert estimate > count_tokens(messages) + count_tokens(schemas)
    assert "secret" not in json.dumps([estimate])


def test_cjk_and_schema_proxy_cold_reservation_covers_observed_13748():
    from mini_agent.budget import request_input_estimate
    # Shape proxy, not a reconstruction of the unsaved #09 request.
    messages = [{"role": "system", "content": "修" * 2502 + "x" * 4101},
                {"role": "user", "content": "u" * 142}]
    schemas = [{"type": "function", "function": {"name": "fixture",
                "description": "y" * 6600, "parameters": {"type": "object", "properties": {}}}}]
    raw = request_input_estimate(messages, schemas, model_id="fixture")
    old = count_tokens(messages) + count_tokens(schemas)
    assert raw > old
    assert 2 * raw + 256 >= 13748
    assert 2 * old + 256 < 13748


def test_estimator_version_cannot_reuse_legacy_warm_samples(tmp_path):
    runtime, _, _, _ = _late_recovery_runtime(tmp_path, 27000)
    legacy = request_key(runtime.model_binding.reference, runtime.executor.registry.schemas(), "work")
    current = runtime._budget_key(runtime.executor.registry.schemas(), "work")
    assert legacy != current
    runtime.task_budget.data["samples"][legacy] = [[2918, 13748]]
    assert runtime.task_budget.data["samples"][current] == [[4000, 7000], [4200, 7200]]
    assert runtime.task_budget.input_reservation(4000, current) == 7956


def test_serialized_trim_preserves_user_constraints_and_complete_tool_pairs(tmp_path):
    from mini_agent.budget import request_input_estimate
    _, _, context = _runtime(tmp_path, lambda *_: {})
    context.protected_messages = [{"role": "system", "content": "protected " * 200}]
    context.history.append({"role": "user", "content": "later correction"})
    for index in range(4):
        context.history.extend([
            {"role": "assistant", "tool_calls": [{"id": str(index), "type": "function",
                "function": {"name": "observe", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": str(index), "content": "中文 result " * 400},
        ])
    estimator = lambda messages: request_input_estimate(messages, [], model_id="fixture")
    floor = context._task_budget_view(10**9, False, observe=False, recent_rounds=0)
    allowance = estimator(floor) + 100
    original = deepcopy(context.history)
    view = context.prepare_messages(input_allowance=allowance, input_estimator=estimator)
    assert estimator(view) <= allowance
    assert any(m.get("content") == "later correction" for m in view)
    calls = [c["id"] for m in view for c in m.get("tool_calls", [])]
    assert calls == [m["tool_call_id"] for m in view if m["role"] == "tool"]
    assert context.history == original
