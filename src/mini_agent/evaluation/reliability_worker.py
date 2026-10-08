"""Isolated worker for one frozen reliability trial.

Offline mode probes the real public boundary APIs with controlled local inputs.
Live mode runs the canonical AgentRuntime with ParentRuntimePolicy; it does not
inherit the coding harness's direct-path validation exemption.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any

from mini_agent.evaluation.evidence import sanitize_text, write_evidence
from mini_agent.evaluation.faults import FaultController
from mini_agent.evaluation.reliability_schema import validate_scenario
from mini_agent.evaluation.reliability_diagnostics import RequestDiagnostics


def _exception_diagnostic(error: BaseException, *, stage: str, binding=None) -> dict[str, Any]:
    """Keep code locations, never traceback text, locals, or credential paths."""
    frames = []
    frame = error.__traceback__
    root = Path(__file__).resolve().parents[2]
    while frame is not None:
        try:
            relative = Path(frame.tb_frame.f_code.co_filename).resolve().relative_to(root)
        except ValueError:
            frame = frame.tb_next
            continue
        frames.append({"file": relative.as_posix(), "line": frame.tb_lineno,
                       "function": frame.tb_frame.f_code.co_name[:80]})
        frame = frame.tb_next
    result = {"stage": stage, "error_kind": type(error).__name__, "frames": frames[-8:]}
    from mini_agent.providers.base import ProviderToolArgumentsError, ProviderToolCallShapeError
    if isinstance(error, ProviderToolCallShapeError):
        result["tool_call_shape"] = dict(error.diagnostic)
    if isinstance(error, ProviderToolArgumentsError):
        result["tool_arguments"] = dict(error.diagnostic)
        result["failure_origin"] = "model_output" if error.model_output_failure else "unconfirmed"
    if isinstance(getattr(error, "errno", None), int):
        result["errno"] = error.errno
    if binding is not None and _provider_infrastructure_category(error):
        provider = binding.provider
        secrets = (provider.api_key, provider.endpoint, binding.profile.model_id,
                   *provider.extra_headers.values())
        result["provider"] = {
            "status": getattr(error, "status", None),
            "detail": sanitize_text(str(getattr(error, "detail", "")), secrets)[:1000],
        }
    return result


def _pending_request_tokens(runtime) -> int:
    from mini_agent.context import count_tokens
    return runtime.request_tokens + count_tokens(runtime.executor.registry.schemas())


def _reliability_protected_messages(workspace: Path, *, recovery: bool = False,
                                    finalization: bool = False) -> list[dict]:
    from mini_agent.prompt import build_system_prompt, build_completion_prompt
    instructions = (
        "这是隔离的可靠性评测 trial。只使用当前工具表与逐项权限规则。"
        "工具失败后按 system policy 调查和恢复；权限拒绝须遵守任务终止规则。"
        "不能绕过 PermissionGate。run_shell 只允许执行 "
        "python -m unittest discover -s tests，且只用于 purpose=verification。"
        "进程只允许使用当前任务提供的 fixture 命令；MCP 连接断开后不得重试。"
        "任务给出明确文件时直接读取；需要定位文件时才用 list_dir 或 grep。不要用 read_file 读取目录。"
        "后台调查结束后须用 get_subagent_result 领取；失败后遵守恢复阶段要求。"
        "每个 tool call 都要读取对应结果，修改后必须完成独立 verification。"
    )
    builder = build_completion_prompt if finalization else build_system_prompt
    messages = [{"role": "system", "content": builder(
        cwd=str(workspace), project_instructions=instructions,
    )}]
    if recovery:
        messages.append({"role": "system", "content": (
            "这是新的 Python 进程，正在处理 crash recovery 派生会话。pending 工具结果只能视为不确定事实，"
            "不得重放。所有 issue 逐项调查并依据用户反馈结算后，必须提交引用 crash_recovery trigger 的新计划；"
            "重新经过当前精确权限规则，执行计划并以独立 verification 完成任务。"
        )})
    return messages


def _observe_tool_failures(registry, diagnostics: list[dict]) -> None:
    from mini_agent.state import PlanRejected
    for tool in registry.list_tools():
        handler = tool.handler
        def observed(*args, _handler=handler, _name=tool.name, **arguments):
            try:
                return _handler(*args, **arguments)
            except PlanRejected:
                raise
            except Exception as error:
                if len(diagnostics) < 32:
                    diagnostics.append({"tool": _name, "arguments_sha256": _sha_json(arguments),
                                        **_exception_diagnostic(error, stage="tool_handler")})
                raise
        tool.handler = observed


def _fixture_process_directory(workspace: Path, cwd: str | None) -> Path:
    candidate = Path(cwd) if cwd is not None else workspace
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=True)
    if resolved != workspace or not resolved.is_dir():
        raise ValueError("reliability fixture cwd must be the trial workspace root")
    return resolved


def _provider_infrastructure_category(error: BaseException) -> str | None:
    """Return a credential-free infrastructure label for provider failures."""
    from mini_agent.providers.base import ProviderHTTPError, ProviderProtocolError, ProviderToolArgumentsError

    if isinstance(error, ProviderToolArgumentsError) and error.model_output_failure:
        return None

    if isinstance(error, ProviderHTTPError):
        return "provider_connection_error" if error.status == 0 else f"provider_http_{error.status}"
    if isinstance(error, ProviderProtocolError):
        return "provider_protocol_error"
    return None


def _record_initial_verification_fault(controller: FaultController, passed: bool | None) -> bool:
    """Record the public-test seam only when the initial code really fails."""
    if passed is not False:
        return False
    return controller.hit(controller.target, evidence={
        "check": "initial_public_test", "result": "failed",
    })


def _record_denied_write_fault(
    controller: FaultController, *, scenario_id: str, tool_name: str,
    arguments: dict[str, Any], message: str | None,
) -> bool:
    """Count only the actual denied write to the frozen protected target."""
    if scenario_id != "permission-denied" or tool_name != "write_file" or not message:
        return False
    raw_path = str(arguments.get("path", "")).replace("\\", "/")
    if "权限拒绝" not in message or not (
        raw_path == "src/locked.py" or raw_path.endswith("/src/locked.py")
    ):
        return False
    return controller.hit(controller.target, evidence={
        "tool": tool_name, "target": "src/locked.py", "permission": "denied",
        "handler_admitted": False,
    })


def _register_declared_plan_tools(registry: Any, state: Any, allowed_tools: tuple[str, ...] | list[str]) -> None:
    """Expose canonical Plan Contract tools only when the frozen scenario declares them."""
    plan_tools = {
        "begin_plan": "make_begin_plan_tool",
        "commit_plan": "make_commit_plan_tool",
        "request_replan": "make_request_replan_tool",
        "update_plan_progress": "make_update_plan_progress_tool",
    }
    requested = [name for name in plan_tools if name in allowed_tools]
    if not requested:
        return
    from mini_agent import tools as tool_builders
    for name in requested:
        registry.register(getattr(tool_builders, plan_tools[name])(state))


def _register_declared_recovery_tool(registry: Any, state: Any,
                                     allowed_tools: tuple[str, ...] | list[str]):
    """Register the canonical RecoveryRuntime tool only on declared surfaces."""
    if "recover" not in allowed_tools:
        return None
    from mini_agent.recovery import RecoveryRuntime, make_recover_tool
    recovery = RecoveryRuntime(state, None)
    registry.register(make_recover_tool(recovery))
    return recovery


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_small_json(path: Path, value: Any, *, limit: int = 64 * 1024) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("reliability metadata exceeds size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False),
    }}


def _offline_probe(scenario: dict[str, Any], workspace: Path, scenario_directory: Path,
                   controller: FaultController) -> dict[str, Any]:
    sid = scenario["scenario_id"]
    observed: dict[str, bool] = {}
    details: dict[str, Any] = {}
    fault_status = "not_triggered"
    cleanup_complete = True
    cleanup_issue = None
    recovery_status = "not_applicable" if not scenario["recovery_steps"] else "incomplete"
    recovery_steps: list[dict[str, Any]] = []
    feedback_events: list[dict[str, Any]] = []
    grader_passed: bool | None = None

    if sid in {"tool-handler-exception", "permission-denied"}:
        from mini_agent.evaluation.worker import NonInteractivePermissionGate
        from mini_agent.permission import DENY, PermissionPolicy
        from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry
        registry = ToolRegistry()
        calls = {"target": 0, "fallback": 0}
        if sid == "tool-handler-exception":
            tool_name = scenario["fault"]["parameters"].get("tool", "read_file")
            def handler(**_arguments):
                calls["target"] += 1
                return "unexpected success"
            registry.register(Tool(tool_name, "controlled handler", {"type": "object", "properties": {}, "additionalProperties": False},
                                   controller.wrap_handler(tool_name, handler)))
            rules = {tool_name: "allow"}
            executor = ToolExecutor(registry, gate=NonInteractivePermissionGate(PermissionPolicy(rules)))
            result = executor.execute_result(tool_name, {})
            observed["tool-result-replayed"] = (
                result.handler_admitted and result.outcome == "failed"
                and result.error_kind == "handler_exception"
                and isinstance(result.output, str) and bool(result.output)
            )
            observed["handler_exception_caught"] = calls["target"] == 1
            recovered = executor.execute_result(tool_name, {})
            observed["subsequent-call-succeeds"] = (
                recovered.handler_admitted and recovered.outcome == "succeeded"
                and calls["target"] == 2
            )
            fault_status = "triggered" if controller.hit_count == 1 else "not_triggered"
            details["execution"] = {
                "first": {"outcome": result.outcome, "error_kind": result.error_kind,
                          "handler_admitted": result.handler_admitted},
                "subsequent": {"outcome": recovered.outcome,
                               "handler_admitted": recovered.handler_admitted},
            }
        else:
            from mini_agent.tools.base import Tool
            def denied_handler(**_arguments):
                calls["target"] += 1
                return "private"
            def fallback_handler(**_arguments):
                calls["fallback"] += 1
                return "public fallback"
            registry.register(Tool("write_file", "denied target", {
                "type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"], "additionalProperties": False,
            }, denied_handler, effect_class="possible"))
            registry.register(Tool("read_file", "public fallback", {
                "type": "object", "properties": {}, "additionalProperties": False,
            }, fallback_handler))
            gate = NonInteractivePermissionGate(PermissionPolicy({
                "write_file": {"*": "allow", "src/locked.py": DENY}, "read_file": "allow",
            }))
            executor = ToolExecutor(registry, gate=gate)
            denied = executor.execute_result("write_file", {"path": "src/locked.py"})
            fallback = executor.execute_result("read_file", {})
            observed["denied-handler-not-run"] = not denied.handler_admitted and denied.permission == "denied" and calls["target"] == 0
            observed["no-implicit-approval"] = calls["target"] == 0 and denied.error_kind == "permission_denied"
            from mini_agent.state import AgentState
            state = AgentState()
            state.begin_task("permission boundary")
            state.record_execution_result(denied)
            observed["permission-stops-task"] = state.status == "failed"
            observed["public_fallback_used"] = fallback.outcome == "succeeded" and calls["fallback"] == 1
            fault_status = "triggered"
            details["denied"] = {"outcome": denied.outcome, "permission": denied.permission,
                                 "handler_admitted": denied.handler_admitted}
            details["fallback"] = {"outcome": fallback.outcome}
        grader_passed = all(observed.values())

    elif sid == "process-wait-timeout":
        from mini_agent.processes import ProcessManager
        from mini_agent.state import AgentState
        from mini_agent.tools.base import ToolExecutor, ToolRegistry
        from mini_agent.tools.process import (
            make_start_process_tool, make_wait_process_tool, make_control_process_tool,
        )
        from mini_agent.agent import ParentRuntimePolicy
        from mini_agent.context import ContextManager
        from types import SimpleNamespace
        from mini_agent.permission import PermissionGate, PermissionPolicy
        manager = ProcessManager(grace_seconds=0.1)
        state = AgentState()
        state.begin_task("process wait timeout boundary")
        state.bind_process_manager(manager)
        task_id = state.ensure_task_id()
        command = f'"{sys.executable}" "{scenario_directory.parent / "support" / "process_fixture.py"}" --seconds 0.8'
        registry = ToolRegistry()
        registry._process_manager = manager
        for tool in (make_start_process_tool(state, manager), make_wait_process_tool(state, manager),
                     make_control_process_tool(state, manager, kill=False),
                     make_control_process_tool(state, manager, kill=True)):
            registry.register(tool)
        executor = ToolExecutor(registry, PermissionGate(PermissionPolicy({
            "start_process": "allow", "wait_process": "allow",
            "terminate_process": "allow", "kill_process": "allow",
        })))
        start_result = executor.execute_result("start_process", {
            "command": command, "cwd": str(workspace), "stdin_mode": "closed",
        }, state=state)
        state.record_execution_result(start_result)
        started_info = json.loads(start_result.output)
        process_id = started_info["process_id"]
        wait_execution = executor.execute_result("wait_process", {
            "process_id": process_id, "timeout_ms": 40,
        }, state=state)
        timeout_result = json.loads(wait_execution.output)
        state.sync_processes(manager.sync_processes(task_id))
        observed["timeout-not-exit"] = timeout_result["status"] == "running" and timeout_result["exit_code"] is None
        context = ContextManager(state, [{"role": "user", "content": "stop the fixture"}], observability=False)
        fake_runtime = SimpleNamespace(
            context=context, executor=SimpleNamespace(registry=registry),
            collect_background_events=lambda: None, session_boundary=None,
        )
        decision = ParentRuntimePolicy().on_text(fake_runtime, "done")
        observed["active-process-blocks-done"] = (
            decision is not None and decision.stop_reason == "awaiting_process"
            and state.status == "awaiting_process" and bool(state.active_process_records())
        )
        terminate = executor.execute_result("terminate_process", {"process_id": process_id}, state=state)
        state.record_execution_result(terminate)
        state.sync_processes(manager.sync_processes(task_id))
        cleanup = manager.cleanup(task_id)
        cleanup_complete = cleanup.complete
        cleanup_issue = None if cleanup.complete else "process_fixture_cleanup_incomplete"
        if observed["timeout-not-exit"]:
            controller.hit(scenario["fault"]["target"], evidence={"reason": timeout_result["reason"],
                                                                    "status": timeout_result["status"]})
        fault_status = "triggered" if controller.hit_count else "not_triggered"
        observed["cleanup-complete"] = cleanup_complete
        grader_passed = all(observed.values())
        details["wait"] = {k: timeout_result.get(k) for k in ("reason", "status", "exit_code")}
        details["agent_decision"] = decision.stop_reason if decision is not None else None

    elif sid == "verification-repair":
        from mini_agent.permission import PermissionGate, PermissionPolicy
        from mini_agent.state import AgentState
        from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry
        test_command = "python -m unittest discover -s tests"
        initial_grade = _run_public_test(workspace, test_command)
        known_good = workspace.parent / f".known-good-{uuid.uuid4().hex}"
        known_good_source = scenario_directory / "known_good"
        shutil.copytree(known_good_source, known_good)
        try:
            known_good_grade = _run_public_test(known_good, test_command)
            repair_source = (known_good / "src" / "example.py").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(known_good, ignore_errors=True)
        state = AgentState()
        state.begin_task("verify after repair")
        registry = ToolRegistry()
        def write_handler(path: str, content: str):
            target = workspace / path
            target.write_text(content, encoding="utf-8")
            return "written"
        def verify_handler(command: str, purpose: str = "execution"):
            if command != test_command or purpose != "verification":
                return "[exit=2] verification command not permitted"
            code = _run_public_test(workspace, command)
            return "[exit=0] tests passed" if code is True else "[exit=1] tests failed"
        registry.register(Tool("write_file", "repair fixture", {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False,
        }, write_handler, effect_class="possible"))
        registry.register(Tool("run_shell", "bounded public verification", {
            "type": "object", "properties": {"command": {"type": "string"},
                                                  "purpose": {"type": "string", "enum": ["execution", "verification"]}},
            "required": ["command"], "additionalProperties": False,
        }, verify_handler, effect_class="possible"))
        executor = ToolExecutor(registry, PermissionGate(PermissionPolicy({
            "write_file": "allow", "run_shell": {"*": "deny", test_command: "allow"},
        })))
        write_result = executor.execute_result("write_file", {"path": "src/example.py", "content": repair_source}, state=state)
        state.record_execution_result(write_result)
        generation_after_repair = state.current_generation_id
        verification = executor.execute_result("run_shell", {"command": test_command, "purpose": "verification"}, state=state)
        state.record_execution_result(verification)
        generation_after_verification = state.current_generation_id
        observed["verification-current-generation"] = (
            verification.exit_code == 0 and state.has_verification_evidence()
            and generation_after_verification > generation_after_repair
            and state.verification_evidence[-1].generation_id == generation_after_verification
        )
        observed["hidden-grader-passes"] = initial_grade is False and known_good_grade is True
        fault_status = "triggered" if initial_grade is False else "not_triggered"
        grader_passed = known_good_grade is True
        details.update({"initial_public_test_passed": initial_grade,
                        "known_good_public_test_passed": known_good_grade,
                        "verification_generation": generation_after_verification,
                        "verification_evidence": state.has_verification_evidence()})

    elif sid.startswith("mcp-"):
        result = _probe_mcp(scenario, scenario_directory, workspace.parent, controller)
        observed.update(result["invariants"])
        fault_status = result["fault_status"]
        cleanup_complete = result["cleanup_complete"]
        cleanup_issue = result["cleanup_issue"]
        details.update(result["details"])
        grader_passed = all(observed.values()) if observed else None

    elif sid == "durable-commit-failure":
        result = _probe_durable_commit_failure(scenario, workspace, controller)
        observed.update(result["invariants"])
        fault_status = result["fault_status"]
        recovery_status = result["recovery_status"]
        recovery_steps = result["recovery_steps"]
        details.update(result["details"])
        grader_passed = all(observed.values()) if observed else None
    elif sid.startswith("crash-") or sid == "recovery-user-decisions":
        result = _probe_recovery(scenario, workspace, scenario_directory, controller)
        observed.update(result["invariants"])
        fault_status = result["fault_status"]
        recovery_status = result["recovery_status"]
        recovery_steps = result["recovery_steps"]
        feedback_events = result.get("feedback_events", [])
        details.update(result["details"])
        grader_passed = all(observed.values()) if observed else None

    elif sid.startswith("subagent-") or sid == "followup-incompatible":
        result = _probe_subagent(scenario, workspace, scenario_directory, controller)
        observed.update(result["invariants"])
        fault_status = result["fault_status"]
        recovery_status = result["recovery_status"]
        cleanup_complete = result["cleanup_complete"]
        cleanup_issue = result["cleanup_issue"]
        details.update(result["details"])
        feedback_events = result.get("feedback_events", [])
        grader_passed = all(observed.values()) if observed else None
    else:
        controller.hit(scenario["fault"]["target"], evidence={"probe": "driver_registered"})
        fault_status = "triggered" if controller.hit_count else "not_triggered"
        observed = {key: False for key in scenario["invariants"]}
        grader_passed = None
        recovery_status = "incomplete"
        details["limitation"] = "no matching offline boundary adapter"

    invariants = [
        {"invariant_id": key, "status": "passed" if observed.get(key) else "failed" if key in observed else "incomplete"}
        for key in scenario["invariants"]
    ]
    statuses = {item["status"] for item in invariants}
    invariant_status = "passed" if statuses == {"passed"} else "failed" if "failed" in statuses else "incomplete"
    if fault_status == "not_triggered":
        recovery_status = "not_triggered"
    # Fixture probes exercise boundary adapters directly, without running an
    # Agent task. Keep task-grader results explicitly unrun.
    grader_passed = None
    return {
        "fault_status": fault_status, "fault_events": list(controller.events),
        "invariant_status": invariant_status, "invariants": invariants,
        "recovery_status": recovery_status, "recovery_steps": recovery_steps,
        "grader_passed": grader_passed, "agent_stop_reason": None,
        "agent_terminal_status": None, "cleanup_complete": cleanup_complete,
        "cleanup_issue": cleanup_issue, "infrastructure_error": None,
        "source_consistent": True, "state_summary": details,
        "trace_summary": {"kind": "offline_component_probe", "sha256": _sha_json(details)},
        "feedback_events": feedback_events, "diff_patch": "",
        "phases": {"offline_probe": {"started_at": _now(), "ended_at": _now(), "usage": None}},
        "artifacts": {"evidence": []}, "manual_review": [],
    }


def _probe_mcp(scenario: dict[str, Any], scenario_directory: Path, artifact_root: Path,
               controller: FaultController) -> dict[str, Any]:
    from mini_agent.mcp.client import McpClient
    from mini_agent.mcp.protocol import McpRemoteError, McpTimeoutError, McpTransportError

    params = scenario["fault"]["parameters"]
    transport = params.get("transport", "stdio")
    support = scenario_directory.parent / "support"
    record = artifact_root / f"mcp-{uuid.uuid4().hex}.record"
    server_process = None
    client = None
    cleanup_complete = True
    cleanup_issue = None
    try:
        if transport == "stdio":
            modes = {
                "mcp-disconnect": "call-disconnect", "mcp-remote-error": "remote-error",
                "mcp-is-error": "is-error", "mcp-call-timeout": "call-silent",
            }
            mode = modes[scenario["scenario_id"]]
            server = {"alias": "reliability", "transport": "stdio", "command": [
                sys.executable, str(support / "mcp_stdio_server.py"), "--mode", mode,
                "--record", str(record),
            ]}
            client = McpClient(server, startup_timeout=1, list_timeout=1,
                               call_timeout=0.08 if mode == "call-silent" else 1,
                               close_timeout=0.4).connect()
        else:
            server_script = support / "mcp_http_server.py"
            ready = artifact_root / f"mcp-{uuid.uuid4().hex}.ready"
            mode = {
                "mcp-disconnect": "disconnect", "mcp-remote-error": "remote-error",
                "mcp-is-error": "is-error", "mcp-call-timeout": "timeout",
            }[scenario["scenario_id"]]
            server_process = subprocess.Popen(
                [sys.executable, str(server_script), "--port", "0", "--mode", mode,
                 "--ready-file", str(ready), "--record", str(record)],
                cwd=str(scenario_directory), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline and server_process.poll() is None:
                time.sleep(0.01)
            if not ready.exists():
                raise RuntimeError("http_fixture_start_failed")
            port = int(ready.read_text(encoding="ascii"))
            client = McpClient({"alias": "reliability", "transport": "http",
                                "url": f"http://127.0.0.1:{port}", "allow_loopback_http": True},
                               startup_timeout=1, list_timeout=1,
                               call_timeout=0.1 if mode in {"timeout", "disconnect"} else 1,
                               close_timeout=0.4).connect()
        tools = client.list_tools()
        if not tools:
            raise RuntimeError("mcp_fixture_has_no_tools")
        if scenario["scenario_id"] == "mcp-is-error":
            result = client.call_tool("echo", {"text": "probe"})
            controller.hit(scenario["fault"]["target"], evidence={"isError": result.get("isError"), "transport": transport})
            invariant_map = {"valid-is-error-result": result.get("isError") is True,
                             "integer-error-sanitized": True}
            fault_status = "triggered" if result.get("isError") is True else "not_triggered"
            details = {"result_is_error": result.get("isError"), "transport": transport}
        else:
            try:
                client.call_tool("echo", {"text": "probe"})
                error_kind, error_text = None, ""
            except McpRemoteError as error:
                error_kind = "remote_error"
                code = error.code
                error_text = str(error)
            except (McpTimeoutError, McpTransportError) as error:
                error_kind = "timeout" if isinstance(error, McpTimeoutError) else "transport"
                code = None
                error_text = str(error)
            else:
                code = None
            expected = {
                "mcp-disconnect": "transport", "mcp-remote-error": "remote_error",
                "mcp-call-timeout": "timeout",
            }[scenario["scenario_id"]]
            triggered = error_kind == expected
            if triggered:
                controller.hit(scenario["fault"]["target"], evidence={"error_kind": error_kind, "transport": transport})
            remote_safe = (scenario["scenario_id"] != "mcp-remote-error" or
                           (error_kind == "remote_error" and "fixture failure" not in error_text))
            invariant_map = {
                "connection-not-reused": not client.connected,
                "no-call-retry": _record_count(record, "tools/call") == 1,
                "integer-error-sanitized": (scenario["scenario_id"] != "mcp-remote-error" or
                                             (error_kind == "remote_error" and isinstance(code, int) and remote_safe)),
                "bounded-timeout": (scenario["scenario_id"] != "mcp-call-timeout" or error_kind == "timeout"),
            }
            fault_status = "triggered" if triggered else "not_triggered"
            details = {"error_kind": error_kind, "transport": transport,
                       "call_requests": _record_count(record, "tools/call"),
                       "client_connected_after_fault": client.connected}
        if client is not None:
            client.close()
        close_report = client.close_report if client is not None else {"closed": True}
        cleanup_complete = bool(close_report.get("closed"))
        if server_process is not None:
            try:
                server_process.terminate()
                server_process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                server_process.kill()
                try:
                    server_process.wait(timeout=0.4)
                except subprocess.TimeoutExpired:
                    cleanup_complete = False
            except Exception:
                cleanup_complete = False
        if not cleanup_complete:
            cleanup_issue = "mcp_client_close_incomplete"
        details["close_report"] = close_report
        if "cleanup-complete" in invariant_map:
            invariant_map["cleanup-complete"] = cleanup_complete
        if "cleanup-complete" not in invariant_map and scenario["scenario_id"] == "mcp-call-timeout":
            invariant_map["cleanup-complete"] = cleanup_complete
        return {"invariants": invariant_map, "fault_status": fault_status,
                "cleanup_complete": cleanup_complete, "cleanup_issue": cleanup_issue,
                "details": details}
    except Exception as error:
        return {"invariants": {}, "fault_status": "injection_error",
                "cleanup_complete": False, "cleanup_issue": type(error).__name__,
                "details": {"error_kind": type(error).__name__, "transport": transport}}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if server_process is not None:
            try:
                server_process.terminate()
                server_process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                server_process.kill()
                try:
                    server_process.wait(timeout=0.4)
                except subprocess.TimeoutExpired:
                    cleanup_complete = False
            except Exception:
                cleanup_complete = False


def _record_count(path: Path, method: str) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                   if line == method or (line.startswith("{") and json.loads(line).get("method") == method))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return -1


def _run_public_test(workspace: Path, command: str) -> bool | None:
    if command != "python -m unittest discover -s tests":
        return None
    if not (workspace / "tests").is_dir():
        return None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=10, check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        # Empty discovery must not silently certify an untested fixture.
        import re
        output = result.stdout.decode("utf-8", errors="replace")
        count = re.search(r"Ran (\d+) tests? in", output)
        return result.returncode == 0 and bool(count and int(count.group(1)) > 0)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _probe_recovery(scenario: dict[str, Any], workspace: Path, scenario_directory: Path,
                    controller: FaultController) -> dict[str, Any]:
    """Exercise real SessionStore/Resume APIs for the frozen crash seams."""
    from mini_agent.context import ContextManager
    from mini_agent.session import DurableToolBoundary, SessionStore
    from mini_agent.state import AgentState
    from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry
    from mini_agent.permission import PermissionGate, PermissionPolicy

    sid = scenario["scenario_id"]
    temp_root = workspace.parent / f".recovery-{uuid.uuid4().hex}"
    temp_root.mkdir(mode=0o700)
    task_workspace = temp_root / "workspace"
    task_workspace.mkdir()
    store = SessionStore(temp_root / "sessions")
    state = AgentState()
    state.begin_task("reliability crash-boundary probe")
    context = ContextManager(state, [{"role": "user", "content": "probe recovery boundary"}], observability=False)
    saved = store.save(None, state, context, workspace_root=task_workspace)
    boundary = DurableToolBoundary(store, saved["session_id"], task_workspace)
    call_id = "reliability-call"
    arguments = {"path": "counter.txt", "content": "1"}
    tool_name = "write_file"
    assistant = {"role": "assistant", "content": None, "tool_calls": [_tool_call(tool_name, arguments, call_id)]}
    context.history.append(assistant)
    call = {"invocation_id": "inv-reliability", "tool_call_id": call_id,
            "tool": tool_name, "arguments": arguments, "effect_class": "possible"}
    feedback_events: list[dict[str, Any]] = []
    try:
        boundary.start_round(1, assistant, [call], state, context)
        admitted = sid != "crash-before-admission"
        registry = ToolRegistry()
        calls = {"count": 0}
        def side_effect(path: str, content: str):
            calls["count"] += 1
            target = task_workspace / path
            existing = int(target.read_text() or "0") if target.exists() else 0
            target.write_text(str(existing + 1), encoding="utf-8")
            return "changed"
        registry.register(Tool("write_file", "counted side effect", {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False,
        }, side_effect, effect_class="possible"))
        executor = ToolExecutor(registry, PermissionGate(PermissionPolicy({"write_file": "allow"})))
        invariants: dict[str, bool] = {}
        if admitted:
            admission = executor.admit(tool_name, arguments, state=state)
            if not hasattr(admission, "reservation"):
                raise RuntimeError("handler_admission_failed")
            boundary.record_admission("inv-reliability", admission, state, context)
            if sid == "crash-after-handler":
                result = executor.execute_admitted(admission)
                state.record_execution_result(result)
                controller.hit(scenario["fault"]["target"], evidence={"side_effect_calls": calls["count"]})
                # Deliberately stop before recording the matching durable tool result.
            elif sid == "crash-after-admission":
                controller.hit(scenario["fault"]["target"], evidence={"handler": "not_entered"})
        source_path = store.path_for(saved["session_id"])
        source_before = source_path.read_bytes()
        if sid == "crash-before-admission":
            controller.hit(scenario["fault"]["target"], evidence={"boundary": "before_handler_admission"})
        recovery = subprocess.run(
            [sys.executable, "-m", "mini_agent.evaluation.reliability_worker",
             "--recover-crash", str(store.root), saved["session_id"], str(task_workspace),
             sid, str(scenario_directory / "feedback.json"),
             scenario["fault"]["parameters"].get("decision_path", "continue")],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
        recovered = json.loads(recovery.stdout.decode("utf-8")) if recovery.returncode == 0 else {}
        invariants = dict(recovered.get("invariants", {}))
        if sid == "crash-after-handler":
            counter = task_workspace / "counter.txt"
            invariants["side-effect-once"] = calls["count"] == 1 and counter.is_file() and counter.read_text(encoding="utf-8") == "1"
        if sid == "crash-after-admission":
            invariants["handler-not-replayed"] = calls["count"] == 0
        if sid == "crash-before-admission":
            invariants["admitted-call-not-replayed"] = calls["count"] == 0
        controller_events = list(controller.events)
        if sid == "recovery-user-decisions" and recovered.get("feedback_events"):
            controller.hit(scenario["fault"]["target"], evidence=recovered["feedback_events"][0])
            controller_events = list(controller.events)
        fault_status = "triggered" if controller.hit_count else "injection_error"
        invariant_values = list(invariants.values())
        if recovery.returncode != 0:
            recovery_status = "failed"
        else:
            recovery_status = recovered.get("recovery_status", "incomplete")
        recovery_steps = recovered.get("recovery_steps", [])
        details = {
            "source_session_id": saved["session_id"],
            "derived_session_id": recovered.get("derived_session_id"),
            "tool_result_outcome": recovered.get("tool_result", {}).get("outcome"),
            "handler_calls": calls["count"],
            "recovery_process_returncode": recovery.returncode,
            "source_session_unchanged": source_path.read_bytes() == source_before
                and recovered.get("source_unchanged") is True,
            "crash_issue_count": recovered.get("crash_issue_count"),
            "recovery_invariants": recovered.get("invariants", {}),
        }
        feedback_events.extend(recovered.get("feedback_events", []))
        return {"invariants": invariants, "fault_status": fault_status,
                "recovery_status": recovery_status, "recovery_steps": recovery_steps,
                "feedback_events": feedback_events, "details": details}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _recover_crash_session(store_root: str, session_id: str, workspace_root: str,
                          scenario_id: str, feedback_path: str,
                          decision_path: str) -> dict[str, Any]:
    """Claim and inspect a crash source in a fresh interpreter process."""
    from mini_agent.resume import prepare_resume
    from mini_agent.session import SessionStore
    from mini_agent.state import PlanRejected
    from mini_agent.tools.base import ExecutionResult
    from mini_agent.user_actions import resolve_crash_issue, script_digest

    store = SessionStore(store_root)
    source_path = store.path_for(session_id)
    source_before = source_path.read_bytes()
    runtime = prepare_resume(store, session_id, workspace_root).claim()
    derived = store.load(runtime.session_id)
    calls = derived.get("tool_boundary", {}).get("calls", [])
    if len(calls) != 1 or not isinstance(calls[0].get("result"), dict):
        raise ValueError("recovered_tool_call_evidence_missing")
    tool_result = calls[0]["result"]
    state = runtime.state
    issues = list(state.unresolved_crash_issues)
    feedback_events: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = [
        {"step": "claim_new_session", "status": "succeeded", "process": "new_python_process"},
        {"step": "inspect_evidence", "status": "succeeded"},
    ]
    single_claim = False
    try:
        second_candidate = prepare_resume(store, session_id, workspace_root)
        second_candidate.claim()
    except Exception:
        single_claim = True

    if scenario_id == "crash-before-admission":
        invariant_values = {
            "not-executed-recorded": tool_result.get("error_kind") == "interrupted_before_handler",
            "admitted-call-not-replayed": not calls[0].get("handler_admitted"),
            "source-session-read-only": source_path.read_bytes() == source_before,
        }
        steps.append({"step": "verify_current_generation", "status": "succeeded"})
        recovery_status = "succeeded" if all(invariant_values.values()) else "failed"
    else:
        feedback_raw = Path(feedback_path).read_bytes()
        feedback = json.loads(feedback_raw.decode("utf-8"))
        if not isinstance(feedback, list) or len(feedback) not in {2, 3}:
            raise ValueError("frozen_feedback_contract_invalid")
        digest = script_digest(feedback_raw)
        rejected = True
        if scenario_id == "recovery-user-decisions":
            if not issues:
                raise ValueError("recovery_issue_missing")
            issue = issues[0]
            issue_id = issue.issue_id
            try:
                resolve_crash_issue(
                    state, issue_id, "continue", feedback[0]["feedback"],
                    source="simulated_user", metadata={
                        "script_sha256": digest, "trigger": feedback[0]["when"],
                        "issue_id": issue_id, "accepted": False,
                    },
                )
                rejected = False
            except PlanRejected:
                feedback_events.append({
                    "source": "simulated_user", "script_sha256": digest,
                    "trigger": feedback[0]["when"], "issue_id": issue_id,
                    "decision": "continue", "accepted": False,
                })
            investigate_index, decision_index = 1, 2
        else:
            issue_id = None
            investigate_index, decision_index = 0, 1
        resolved_count = 0
        for issue in issues:
            selected_id = issue.issue_id
            investigation = resolve_crash_issue(
                state, selected_id, "investigate", feedback[investigate_index]["feedback"],
                source="simulated_user", metadata={
                    "script_sha256": digest, "trigger": feedback[investigate_index]["when"],
                    "issue_id": selected_id, "accepted": True,
                },
            )
            feedback_events.append({
                "source": investigation.source, "script_sha256": digest,
                "trigger": feedback[investigate_index]["when"],
                "issue_id": selected_id, "decision": "investigate", "accepted": True,
            })
            source_text = Path(workspace_root, "src", "example.py")
            observed = source_text.read_text(encoding="utf-8") if source_text.is_file() else "fixture absent"
            attempt = state.record_execution_result(ExecutionResult(
                "read_file", {"path": "src/example.py"}, "allowed", True,
                "succeeded", 0, "none", observed, observed[:200],
            ))
            selected_decision = decision_path if scenario_id == "recovery-user-decisions" else "continue"
            decision = resolve_crash_issue(
                state, selected_id, selected_decision, feedback[decision_index]["feedback"],
                source="simulated_user", metadata={
                    "script_sha256": digest, "trigger": feedback[decision_index]["when"],
                    "issue_id": selected_id, "accepted": True,
                },
            )
            feedback_events.append({
                "source": decision.source, "script_sha256": digest,
                "trigger": feedback[decision_index]["when"],
                "issue_id": selected_id, "decision": selected_decision,
                "accepted": decision.status == "accepted",
                "investigation_attempt_id": attempt.attempt_id,
            })
            resolved_count += int(decision.status == "accepted")
        if scenario_id == "recovery-user-decisions":
            decision_ok = (
                state.status == "blocked" if decision_path == "block"
                else state.planning_state.phase == "exploring"
            )
            invariant_values = {
                "invalid-user-feedback-rejected": rejected,
                "decision-source-recorded": len(state.crash_decisions) >= 2,
                "block-stops-task": decision_path != "block" or state.status == "blocked",
                "single-source-claim": single_claim,
            }
            steps.extend([
                {"step": "resolve_issues", "status": "succeeded" if resolved_count == len(issues) and decision_ok else "failed"},
                {"step": "continue_parent_investigation", "status": "succeeded" if decision_path == "continue" else "not_applicable"},
                {"step": "replan", "status": "succeeded" if decision_path == "continue" else "not_applicable"},
                {"step": "reauthorize", "status": "pending" if decision_path == "continue" else "not_applicable"},
                {"step": "verify_current_generation", "status": "pending" if decision_path == "continue" else "not_applicable"},
            ])
            recovery_status = (
                "failed" if not all(invariant_values.values()) else
                "incomplete" if decision_path == "continue" else "succeeded"
            )
        else:
            invariant_values = {
                "admitted-call-not-replayed": tool_result.get("outcome") == "uncertain",
                "source-session-read-only": source_path.read_bytes() == source_before,
                "all-recovery-issues-explicitly-continued": resolved_count == len(issues) and bool(issues),
                "single-source-claim": single_claim,
            }
            steps.extend([
                {"step": "resolve_issues", "status": "succeeded" if invariant_values["all-recovery-issues-explicitly-continued"] else "failed"},
                {"step": "replan", "status": "pending"},
                {"step": "reauthorize", "status": "pending"},
                {"step": "verify_current_generation", "status": "pending"},
            ])
            recovery_status = "incomplete" if all(invariant_values.values()) else "failed"
    return {
        "tool_result": tool_result, "invariants": invariant_values,
        "recovery_status": recovery_status, "recovery_steps": steps,
        "feedback_events": feedback_events,
        "source_unchanged": source_path.read_bytes() == source_before,
        "derived_session_id": runtime.session_id,
        "used_llm_calls": state.delegation_budget.used_llm_calls,
        "crash_issue_count": len(issues),
        "crash_decision_count": len(state.crash_decisions),
    }


def _probe_durable_commit_failure(scenario: dict[str, Any], workspace: Path,
                                  controller: FaultController) -> dict[str, Any]:
    """Fail a real SessionStore write at each committed tool boundary."""
    from mini_agent.agent import ParentRuntimePolicy
    from mini_agent.context import ContextManager
    from mini_agent.permission import PermissionGate, PermissionPolicy
    from mini_agent.runtime import AgentRuntime
    from mini_agent.session import DurableToolBoundary, SessionStore
    from mini_agent.state import AgentState
    from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry

    boundary_name = scenario["fault"]["parameters"].get("boundary", "admission")
    fail_ordinal = {"admission": 2, "per_call": 3, "round_commit": 6}[boundary_name]
    temp_root = workspace.parent / f".durable-failure-{uuid.uuid4().hex}"
    temp_root.mkdir(mode=0o700)
    durable_workspace = temp_root / "workspace"
    durable_workspace.mkdir()
    store = SessionStore(temp_root / "sessions")
    state = AgentState()
    state.begin_task("durable commit failure probe")
    context = ContextManager(
        state, [{"role": "user", "content": "perform two bounded writes"}],
        observability=False,
    )
    initial = store.save(None, state, context, workspace_root=durable_workspace)
    boundary = DurableToolBoundary(store, initial["session_id"], durable_workspace)
    calls = {"handler": 0, "model": 0}
    registry = ToolRegistry()

    def write_handler(path: str, content: str) -> str:
        calls["handler"] += 1
        (durable_workspace / path).write_text(content, encoding="utf-8")
        return "written"

    registry.register(Tool(
        "write_file", "counted write", {
            "type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"},
            }, "required": ["path", "content"], "additionalProperties": False,
        }, write_handler, effect_class="possible",
    ))
    executor = ToolExecutor(registry, PermissionGate(PermissionPolicy({"write_file": "allow"})))

    def parent_client(_messages, **_options):
        calls["model"] += 1
        if calls["model"] == 1:
            return {"role": "assistant", "content": None, "tool_calls": [
                _tool_call("write_file", {"path": "one.txt", "content": "1"}, "write-1"),
                _tool_call("write_file", {"path": "two.txt", "content": "2"}, "write-2"),
            ]}
        return {"role": "assistant", "content": "unreachable"}

    save_count = {"value": 0}
    original_save = store.save

    def failing_save(*args, **kwargs):
        save_count["value"] += 1
        if save_count["value"] == fail_ordinal:
            controller.durable_commit_hook(boundary_name)
        return original_save(*args, **kwargs)

    store.save = failing_save
    runtime = AgentRuntime(
        llm_client=parent_client, context=context, executor=executor,
        policy=ParentRuntimePolicy(), max_rounds=4, session_boundary=boundary,
    )
    try:
        runtime.run()
        stop_reason = "unexpected_return"
    except OSError:
        stop_reason = "commit_error"
    except Exception as error:
        stop_reason = type(error).__name__

    expected_handlers = {"admission": 0, "per_call": 1, "round_commit": 2}[boundary_name]
    prefix_matches = calls["handler"] == expected_handlers
    no_later_action = calls["model"] == 1 and (
        boundary_name != "admission" or calls["handler"] == 0
    ) and (
        boundary_name != "per_call" or calls["handler"] == 1
    )
    try:
        saved = store.load(initial["session_id"])
        source_prefix_matches = (
            saved.get("tool_boundary", {}).get("status") == "pending"
            and len([item for item in saved.get("tool_boundary", {}).get("calls", [])
                     if item.get("status") == "committed"])
            == (0 if boundary_name in {"admission", "per_call"} else 2)
        )
    except Exception:
        source_prefix_matches = False
    invariants = {
        "no-action-after-commit-failure": controller.hit_count == 1 and no_later_action,
        "per-call-order-preserved": prefix_matches and source_prefix_matches,
    }
    fault_status = "triggered" if controller.hit_count == 1 else "not_triggered"
    return {
        "invariants": invariants, "fault_status": fault_status,
        "recovery_status": "succeeded" if all(invariants.values()) else "failed",
        "recovery_steps": [], "details": {
            "boundary": boundary_name, "save_attempts": save_count["value"],
            "handler_calls": calls["handler"], "model_calls": calls["model"],
            "runtime_stop": stop_reason,
            "durable_call_statuses": [item.get("status") for item in boundary.boundary.get("calls", [])],
        },
    }


def _probe_subagent(scenario: dict[str, Any], workspace: Path, scenario_directory: Path,
                    controller: FaultController) -> dict[str, Any]:
    """Exercise background subagent lifecycle using its real Runtime and manager."""
    from threading import Event
    from types import SimpleNamespace

    from mini_agent.agent import ParentRuntimePolicy
    from mini_agent.agent_profiles import AgentProfileCatalog
    from mini_agent.delegation import DelegationManager
    from mini_agent.permission import ALLOW, PermissionGate, PermissionPolicy
    from mini_agent.skills import SkillCatalog
    from mini_agent.state import AgentState
    from mini_agent.tools.base import ToolExecutor, ToolRegistry
    from mini_agent.tools.calc import calculate_tool
    from mini_agent.tools.delegation import make_background_subagent_tools

    sid = scenario["scenario_id"]
    state = AgentState()
    state.begin_task("reliability background subagent probe")
    registry = ToolRegistry()
    registry.register(calculate_tool)
    call_count = {"general": 0, "skill": 0}
    child_started = Event()
    release_child = Event()

    def report_client(messages, **_options):
        request = next(json.loads(item["content"]) for item in messages
                       if item.get("role") == "user" and item.get("content", "").startswith("{"))
        role = request["contract"].get("agent_profile") or "general"
        call_count["skill" if role == "skill-review" else "general"] += 1
        child_started.set()
        if sid in {"subagent-cancel", "subagent-interrupted"}:
            release_child.wait(2)
        return {"role": "assistant", "content": json.dumps({
            "summary": "bounded reliability report", "findings": [],
            "evidence": [], "limitations": [],
        })}

    child_client = controller.wrap_client(report_client) if sid == "subagent-timeout" else report_client
    configured_profiles = {}
    if sid == "followup-incompatible":
        skill_dir = workspace / "skills" / "reliability-skill"
        if not (skill_dir / "SKILL.md").is_file():
            raise ValueError("frozen_skill_fixture_missing")
        configured_profiles["skill-review"] = {
            "description": "Reliability fixture role",
            "prompt": "Inspect the frozen fixture scope.",
            "tools": ["calculate"], "skills": ["reliability-skill"],
        }
    skill_catalog = SkillCatalog(workspace)
    profile_catalog = AgentProfileCatalog(configured_profiles)
    manager = DelegationManager(
        workspace, subagent_llm=child_client, parent_registry=registry,
        parent_state=state, agent_profile_catalog=profile_catalog,
        skill_catalog=skill_catalog,
    )
    registry._delegation_manager = manager
    tools = make_background_subagent_tools(state, manager)
    for tool in tools:
        registry.register(tool)
    gate = PermissionGate(PermissionPolicy({
        "spawn_subagent": ALLOW, "followup_subagent": ALLOW,
        "get_subagent_status": ALLOW, "get_subagent_result": ALLOW,
        "cancel_subagent": ALLOW, "skill": {"*": ALLOW},
    }))
    executor = ToolExecutor(registry, gate)
    arguments = {
        "goal": "inspect the fixed reliability fixture",
        "scope": ["."], "constraints": [], "expected_findings": [],
        "requested_tools": ["calculate"], "selected_parent_facts": [],
        "purpose": "investigation", "agent_profile": "general",
    }
    invariants: dict[str, bool] = {}
    recovery_status = "incomplete"
    recovery_steps: list[dict[str, Any]] = []
    feedback_events: list[dict[str, Any]] = []
    cleanup_complete = True
    cleanup_issue = None

    def start(contract: dict[str, Any]) -> str:
        result = executor.execute_result("spawn_subagent", contract, state=state)
        if result.outcome != "succeeded":
            raise RuntimeError("background_spawn_rejected")
        accepted = json.loads(result.output)
        if not accepted.get("accepted"):
            raise RuntimeError("background_spawn_not_accepted")
        child_id = accepted["child_session_id"]
        manager.confirm_background_startup(child_id)
        manager.commit_background_spawn_round()
        manager.activate_background_tasks()
        return child_id

    def collect(child_id: str) -> dict[str, Any]:
        if not manager.wait(2):
            raise TimeoutError("background_worker_did_not_settle")
        manager.collect_background_events(dispatch=False)
        return manager.background_status(child_id, state)

    def claim(child_id: str) -> tuple[str, dict[str, Any]]:
        result = executor.execute_result(
            "get_subagent_result", {"child_session_id": child_id}, state=state,
        )
        if result.outcome != "succeeded":
            raise RuntimeError("background_result_claim_failed")
        parsed = json.loads(result.output)
        if parsed.get("result_id"):
            state.commit_delegation_tool_result(result.output)
            manager.mark_background_claimed(child_id, parsed["result_id"])
        return result.output, parsed

    try:
        if sid == "followup-incompatible":
            general_id = start(arguments)
            collect(general_id)
            claim(general_id)
            skill_arguments = dict(arguments, agent_profile="skill-review")
            skill_id = start(skill_arguments)
            collect(skill_id)
            claim(skill_id)
            before_calls = call_count["skill"]
            skill_path = workspace / "skills" / "reliability-skill" / "SKILL.md"
            skill_path.write_text(
                "---\nname: reliability-skill\ndescription: Replaced fixture\n---\nchanged\n",
                encoding="utf-8",
            )
            followup_contract = {
                "child_session_id": skill_id, "goal": "continue frozen investigation",
                "scope": ["."], "constraints": [], "expected_findings": [],
                "requested_tools": ["calculate"], "selected_parent_facts": [],
                "purpose": "investigation",
            }
            rejected = executor.execute_result("followup_subagent", followup_contract, state=state)
            invariants["skill-change-rejected-before-llm"] = (
                rejected.outcome == "invalid" and call_count["skill"] == before_calls
            )
            general_followup = dict(followup_contract, child_session_id=general_id)
            accepted = executor.execute_result("followup_subagent", general_followup, state=state)
            accepted_value = json.loads(accepted.output)
            invariants["other-child-session-unaffected"] = bool(accepted_value.get("accepted"))
            if accepted_value.get("accepted"):
                manager.confirm_background_startup(general_id)
                manager.commit_background_spawn_round()
                manager.activate_background_tasks()
                collect(general_id)
                claim(general_id)
            controller.hit(scenario["fault"]["target"], evidence={
                "skill_calls": call_count["skill"], "rejected_outcome": rejected.outcome,
            })
            fault_status = "triggered" if controller.hit_count else "not_triggered"
            recovery_status = "succeeded" if all(invariants.values()) else "failed"
            details = {"calls": call_count, "changed_skill_followup_outcome": rejected.outcome,
                       "unaffected_followup_accepted": bool(accepted_value.get("accepted"))}
        else:
            child_id = None if sid == "subagent-interrupted" else start(arguments)
            if sid == "subagent-cancel":
                if not child_started.wait(2):
                    raise TimeoutError("child_call_barrier_not_reached")
                status_before = manager.background_status(child_id, state)["status"]
                cancel = manager.cancel_background(child_id, "reliability_cancel")
                release_child.set()
                status = collect(child_id) if child_id is not None else {}
                raw, parsed = claim(child_id)
                invariants["cooperative-cancel"] = (
                    status_before == "running" and cancel["cancel_requested"]
                    and parsed.get("outcome") == "cancelled"
                    and state.background_subagent_records()[0].delivery_status == "committed"
                )
                invariants["result-available-after-cancel"] = bool(parsed.get("result_id"))
                invariants["cleanup-complete"] = manager.wait(2)
                fault_status = "triggered" if child_started.is_set() else "not_triggered"
                if fault_status == "triggered":
                    controller.hit(scenario["fault"]["target"], evidence={
                        "cancel_status": status["status"], "result_id": parsed.get("result_id"),
                    })
                details = {"status": status, "result_outcome": parsed.get("outcome"),
                           "result_id": parsed.get("result_id")}
            else:
                status = collect(child_id)
                if sid == "subagent-unclaimed":
                    from mini_agent.context import ContextManager
                    from mini_agent.session import SessionStore, SessionValidationError

                    context = SimpleNamespace(state=state)
                    runtime = SimpleNamespace(
                        context=context, executor=executor, session_boundary=None,
                        collect_background_events=lambda: manager.collect_background_events(dispatch=False),
                    )
                    decision = ParentRuntimePolicy().on_text(runtime, "done")
                    safety_issues = state.session_safety_issues()
                    safe_store = SessionStore(workspace.parent / f".unclaimed-save-{uuid.uuid4().hex}")
                    try:
                        safe_store.save(
                            None, state,
                            ContextManager(state, [{"role": "user", "content": "must not save"}],
                                           observability=False),
                            workspace_root=workspace,
                        )
                        safe_point_rejected = False
                    except SessionValidationError:
                        safe_point_rejected = True
                    first_raw, first = claim(child_id)
                    usage_after_first = state.delegation_budget.used_llm_calls
                    second_raw, second = claim(child_id)
                    usage_after_second = state.delegation_budget.used_llm_calls
                    record = state.background_subagent_records()[0]
                    invariants["unclaimed-blocks-done"] = (
                        decision is not None and decision.stop_reason == "awaiting_subagents"
                    )
                    invariants["safe-point-blocked"] = safe_point_rejected and bool(safety_issues)
                    invariants["repeat-claim-stable"] = (
                        first.get("result_id") == second.get("result_id") == record.result_id
                        and _sha_json(json.loads(first_raw)) == _sha_json(json.loads(second_raw))
                        and usage_after_first == usage_after_second
                    )
                    controller.hit(scenario["fault"]["target"], evidence={
                        "status": status["status"], "result_id": record.result_id,
                    })
                    details = {"status_before_claim": status, "safety_issue_count": len(safety_issues),
                               "result_id": record.result_id, "used_llm_calls": usage_after_first}
                elif sid == "subagent-timeout":
                    raw, parsed = claim(child_id)
                    record = state.background_subagent_records()[0]
                    injected_calls = [{"component": "subagent_llm", "source": "injected",
                                       "counted_as_real": False, "count": 1}]
                    invariants["failed-subagent-claimed"] = (
                        parsed.get("outcome") == "timed_out"
                        and record.delivery_status == "committed"
                    )
                    invariants["usage-settled-once"] = (
                        state.delegation_budget.used_llm_calls == record.usage.llm_calls
                        and record.usage.llm_calls <= 1
                    )
                    fault_status = "triggered" if controller.hit_count == 1 else "not_triggered"
                    details = {"result_outcome": parsed.get("outcome"), "usage": {
                                   "llm_calls": record.usage.llm_calls,
                                   "tool_calls": record.usage.tool_calls,
                                   "tokens": record.usage.tokens,
                               },
                               "injected_calls": injected_calls}
                elif sid == "subagent-interrupted":
                    from mini_agent.context import ContextManager
                    from mini_agent.runtime import AgentRuntime
                    from mini_agent.session import DurableToolBoundary, SessionStore

                    temp_root = workspace.parent / f".subagent-interrupted-{uuid.uuid4().hex}"
                    temp_root.mkdir(mode=0o700)
                    store = SessionStore(temp_root / "sessions")
                    context = ContextManager(
                        state, [{"role": "user", "content": "start background child"}],
                        observability=False,
                    )
                    initial = store.save(None, state, context, workspace_root=workspace)
                    boundary = DurableToolBoundary(store, initial["session_id"], workspace)
                    parent_calls = {"count": 0}

                    def parent_client(_messages, **_options):
                        parent_calls["count"] += 1
                        if parent_calls["count"] == 1:
                            return {"role": "assistant", "content": None, "tool_calls": [
                                _tool_call("spawn_subagent", arguments, "spawn-interrupted"),
                            ]}
                        return {"role": "assistant", "content": "等待后台 worker"}

                    runtime = AgentRuntime(
                        llm_client=parent_client, context=context, executor=executor,
                        policy=ParentRuntimePolicy(), max_rounds=4, session_boundary=boundary,
                    )
                    parent_result = runtime.run()
                    if parent_result.stop_reason != "awaiting_subagents" or not child_started.wait(2):
                        raise RuntimeError("committed_background_barrier_missing")
                    source = store.load(initial["session_id"])
                    source_path = store.path_for(initial["session_id"])
                    source_bytes = source_path.read_bytes()
                    raw_record = source["state"]["delegation_records"][0]
                    committed_boundary = (
                        source.get("save_kind") == "tool_boundary"
                        and source.get("tool_boundary", {}).get("status") == "committed"
                        and raw_record.get("startup_confirmed") is True
                    )
                    if not committed_boundary:
                        raise RuntimeError("background_confirmation_not_durably_committed")
                    controller.hit(scenario["fault"]["target"], evidence={
                        "tool_boundary_status": source["tool_boundary"]["status"],
                        "startup_confirmed": raw_record["startup_confirmed"],
                    })
                    recovery = subprocess.run(
                        [sys.executable, "-m", "mini_agent.evaluation.reliability_worker",
                         "--recover-subagent", str(store.root), initial["session_id"], str(workspace),
                         str(scenario_directory / "feedback.json")],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=10, check=False,
                    )
                    recovered_payload = json.loads(recovery.stdout.decode("utf-8")) if recovery.returncode == 0 else {}
                    feedback_events.extend(recovered_payload.get("user_actions", []))
                    invariants["interrupted-no-worker"] = (
                        recovery.returncode == 0
                        and recovered_payload.get("delivery_status") == "interrupted"
                        and recovered_payload.get("result_id") is None
                        and call_count["general"] == 1
                    )
                    invariants["unknown-usage-conservative"] = (
                        recovered_payload.get("used_llm_calls", 0)
                        >= recovered_payload.get("reserved_llm_calls", 1)
                    )
                    invariants["issue-explicitly-resolved"] = (
                        recovered_payload.get("issue_count", 0) > 0
                        and recovered_payload.get("resolved_issue_count") == recovered_payload.get("issue_count")
                        and recovered_payload.get("user_action_source") == "simulated_user"
                    )
                    invariants["source-session-read-only"] = (
                        source_path.read_bytes() == source_bytes
                        and recovered_payload.get("source_unchanged") is True
                    )
                    recovery_steps.extend([
                        {"step": "claim_new_session", "status": "succeeded" if recovery.returncode == 0 else "failed",
                         "process": "new_python_process"},
                        {"step": "inspect_evidence", "status": "succeeded" if recovered_payload else "failed"},
                        {"step": "resolve_issues", "status": "succeeded" if invariants["issue-explicitly-resolved"] else "failed"},
                    ])
                    fault_status = "triggered" if controller.hit_count else "not_triggered"
                    recovery_status = "succeeded" if all(invariants.values()) else "failed"
                    details = {"source_session_id": initial["session_id"],
                               "derived_session_id": recovered_payload.get("derived_session_id"),
                               "delivery_status": recovered_payload.get("delivery_status"),
                               "result_id": recovered_payload.get("result_id"),
                               "parent_calls": parent_calls["count"],
                               "recovery_returncode": recovery.returncode}
                    child_id = state.background_subagent_records()[0].subagent_id
                    manager.cancel_background(child_id, "simulated_parent_crash")
                    release_child.set()
                    cleanup_complete = manager.wait(2)
                    shutil.rmtree(temp_root, ignore_errors=True)
                else:
                    fault_status = "not_triggered"
                    details = {"status": status}
            if sid != "subagent-interrupted":
                if sid != "subagent-timeout":
                    controller.hit(scenario["fault"]["target"], evidence={"status": status["status"]})
                    fault_status = "triggered" if controller.hit_count else "not_triggered"
                recovery_status = "succeeded" if all(invariants.values()) else "failed"
        if sid == "subagent-timeout":
            # The injected client is the fault. Its event is evidence that the
            # child Runtime reached the named call boundary.
            fault_status = "triggered" if controller.hit_count else "not_triggered"
            recovery_status = "succeeded" if all(invariants.values()) else "failed"
    except Exception as error:
        invariants = {key: False for key in scenario["invariants"]}
        fault_status = "injection_error" if controller.hit_count == 0 else "triggered"
        recovery_status = "failed"
        frame = error.__traceback__
        while frame is not None and frame.tb_next is not None:
            frame = frame.tb_next
        details = {
            "probe_error": type(error).__name__,
            "probe_location": (
                f"{Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}"
                if frame is not None else "unknown",
            ),
        }
    finally:
        release_child.set()
        cleanup = manager.cleanup_background(state.task_id, timeout=2, abandon=True)
        cleanup_complete = bool(cleanup.get("complete"))
        if not cleanup_complete:
            cleanup_issue = "subagent_cleanup_incomplete"
    for key in scenario["invariants"]:
        invariants.setdefault(key, False)
    return {
        "invariants": invariants, "fault_status": fault_status,
        "recovery_status": recovery_status, "cleanup_complete": cleanup_complete,
        "cleanup_issue": cleanup_issue, "recovery_steps": recovery_steps,
        "feedback_events": feedback_events, "details": details,
    }


def _run_live(request: dict[str, Any]) -> dict[str, Any]:
    """Run the frozen live task with only its declared tools and managers."""
    from mini_agent.agent import ParentRuntimePolicy
    from mini_agent.context import ContextBudget, ContextManager
    from mini_agent.permission import PermissionPolicy
    from mini_agent.processes import ProcessManager
    from mini_agent.providers.catalog import ProviderCatalog
    from mini_agent.runtime import AgentRuntime, RuntimeDecision, ToolRoundPlan
    from mini_agent.state import AgentState
    from mini_agent.tools.base import ControlledToolResult, ExecutionResult, Tool, ToolExecutor, ToolRegistry
    from mini_agent.evaluation.worker import NonInteractivePermissionGate, _build_registry, _llm_client
    from mini_agent.evaluation.schema import TrialRequest

    scenario = validate_scenario(request["scenario"])
    workspace = Path(request["workspace"]).resolve(strict=True)
    scenario_directory = Path(request["scenario_directory"]).resolve(strict=True)
    support = scenario_directory.parent / "support"
    state = AgentState()
    state.begin_task(scenario.task)
    controller = FaultController(scenario.fault)
    injected_calls: list[dict[str, Any]] = []
    tool_diagnostics: list[dict] = []
    registry = ToolRegistry()
    public_command = "python -m unittest discover -s tests"
    initial_public_test: bool | None = None
    if scenario.scenario_id == "verification-repair":
        initial_public_test = _run_public_test(workspace, public_command)
        _record_initial_verification_fault(controller, initial_public_test)
    if scenario.scenario_id == "process-wait-timeout":
        fixture_copy = workspace / "support" / "process_fixture.py"
        fixture_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(support / "process_fixture.py", fixture_copy)
    locked_before = (workspace / "src" / "locked.py").read_bytes() if scenario.scenario_id == "permission-denied" else None
    denied_target_hashes = set()
    coding_tools = [name for name in scenario.allowed_tools if name in {
        "read_file", "list_dir", "grep", "write_file", "edit_file", "calculate",
    }]
    if coding_tools:
        scoped_registry = _build_registry({"allowed_tools": coding_tools}, workspace)
        for name in coding_tools:
            tool = scoped_registry.get(name)
            original = tool.handler
            if scenario.scenario_id == "tool-handler-exception":
                tool.handler = controller.wrap_handler(name, original)
            elif scenario.scenario_id == "crash-after-handler" and name in {"write_file", "edit_file"}:
                def crash_after_handler(_handler=original, _tool_name=name, **arguments):
                    result = _handler(**arguments)
                    if controller.hit(scenario.fault["target"], evidence={
                        "tool": _tool_name, "workspace_effect": "file_handler_returned",
                    }):
                        runtime._refresh_usage()
                        meta_path = Path(request["crash_meta_path"])
                        metadata = {
                            "session_id": session_id, "source_task_id": state.task_id,
                            "side_effect_count": 1, "fault_events": list(controller.events),
                            "pre_crash_usage": {
                                "rounds": runtime.rounds, "llm_calls": runtime.llm_calls,
                                "tool_calls": runtime.tool_calls + 1,
                                "elapsed_wall_ms": int((time.monotonic() - started) * 1000),
                                "request_diagnostics": request_diagnostics.snapshot(runtime),
                                "input_tokens": runtime.input_tokens,
                                "output_tokens": runtime.output_tokens,
                                "token_accounting": runtime.token_accounting,
                                "binding_ref": binding.reference.to_dict(),
                            },
                        }
                        _write_small_json(meta_path, metadata)
                        os._exit(86)
                    return result
                tool.handler = crash_after_handler
            registry.register(tool)

    from mini_agent.tools.shell import run_shell_tool
    if "run_shell" in scenario.allowed_tools:
        def bounded_shell(command: str, purpose: str = "execution"):
            if command != public_command or purpose != "verification":
                return "[exit=126] reliability fixture permits only its public verification command"
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                    cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, timeout=30, check=False,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                )
                output = result.stdout[-4000:]
                return f"[exit={result.returncode}] {output}"
            except subprocess.TimeoutExpired:
                return "[timeout] public verification exceeded 30 seconds"
        registry.register(Tool(
            "run_shell", run_shell_tool.description, deepcopy(run_shell_tool.parameters),
            bounded_shell, effect_class="possible",
        ))

    process_manager = None
    if any(name in scenario.allowed_tools for name in {
        "start_process", "get_process", "read_process", "list_processes", "wait_process",
        "write_process", "terminate_process", "kill_process",
    }):
        from mini_agent.tools.process import (
            make_control_process_tool, make_get_process_tool, make_list_processes_tool,
            make_read_process_tool, make_start_process_tool, make_wait_process_tool,
            make_write_process_tool,
        )
        import shlex
        process_manager = ProcessManager(grace_seconds=0.2)
        state.bind_process_manager(process_manager)
        process_tools = {
            "start_process": make_start_process_tool(state, process_manager),
            "get_process": make_get_process_tool(state, process_manager),
            "read_process": make_read_process_tool(state, process_manager),
            "list_processes": make_list_processes_tool(state, process_manager),
            "wait_process": make_wait_process_tool(state, process_manager),
            "write_process": make_write_process_tool(state, process_manager),
            "terminate_process": make_control_process_tool(state, process_manager, kill=False),
            "kill_process": make_control_process_tool(state, process_manager, kill=True),
        }
        if "start_process" in scenario.allowed_tools:
            start_tool = process_tools["start_process"]
            start_handler = start_tool.handler
            fixture_command = [sys.executable, str(fixture_copy), "--wait-for-stop"]
            public_fixture_command = ["python", "support/process_fixture.py", "--wait-for-stop"]
            original_validator = start_tool.argument_validator
            def validate_start(arguments):
                try:
                    selected = shlex.split(arguments["command"])
                    if (selected not in (fixture_command, public_fixture_command)
                            or arguments.get("stdin_mode", "closed") != "closed"):
                        raise ValueError("reliability fixture permits only its task-owned process")
                    _fixture_process_directory(workspace, arguments.get("cwd"))
                    if original_validator is not None:
                        original_validator(arguments)
                except Exception as error:
                    if len(tool_diagnostics) < 32:
                        tool_diagnostics.append({"tool": "start_process", "arguments_sha256": _sha_json(arguments),
                                                 **_exception_diagnostic(error, stage="tool_validation")})
                    raise
            start_tool.argument_validator = validate_start
            def restricted_start(command: str, cwd: str | None = None, stdin_mode: str = "closed"):
                # Recheck at execution; always launch in the validated trial root.
                validate_start({"command": command, "cwd": cwd, "stdin_mode": stdin_mode})
                return start_handler(shlex.join(fixture_command), str(workspace), "closed")
            start_tool.handler = restricted_start
        if "wait_process" in scenario.allowed_tools:
            wait_tool = process_tools["wait_process"]
            original_wait = wait_tool.handler
            def observed_wait(**arguments):
                raw = original_wait(**arguments)
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError):
                    return raw
                if value.get("reason") == "still_running":
                    controller.hit(scenario.fault["target"], evidence={
                        "process_status": value.get("status"), "exit_code": value.get("exit_code"),
                    })
                return raw
            wait_tool.handler = observed_wait
        for name in scenario.allowed_tools:
            if name in process_tools:
                registry.register(process_tools[name])
        registry._process_manager = process_manager

    mcp_manager = None
    mcp_record = workspace.parent / "mcp-live.record"
    if "mcp_reliability_echo" in scenario.allowed_tools:
        from mini_agent.mcp.adapter import assemble_mcp_tools
        transport = request.get("variant", {}).get("transport", "stdio")
        server = {
            "alias": "reliability", "agent_enabled": True, "transport": "stdio",
            "command": [sys.executable, str(support / "mcp_stdio_server.py"),
                        "--mode", "call-disconnect", "--record", str(mcp_record)],
            "call_timeout": 2,
        }
        if transport != "stdio":
            raise ValueError("live mcp-disconnect is frozen to stdio")
        tools, mcp_manager = assemble_mcp_tools([server], occupied_names={item.name for item in registry.list_tools()})
        for tool in tools:
            original = tool.handler
            def observed_mcp(*, _handler=original, **arguments):
                result = _handler(**arguments)
                if isinstance(result, ControlledToolResult):
                    try:
                        payload = json.loads(result.output)
                    except (TypeError, ValueError):
                        payload = {}
                    if payload.get("error_kind") == "mcp_disconnect":
                        controller.hit(scenario.fault["target"], evidence={
                            "error_kind": payload["error_kind"], "alias": payload.get("alias"),
                        })
                return result
            tool.handler = observed_mcp
            registry.register(tool)
        registry._mcp_manager = mcp_manager

    child_provider_catalog = None
    child_binding = None
    delegation_manager = None
    if "spawn_subagent" in scenario.allowed_tools:
        from mini_agent import config as runtime_config
        from mini_agent.agent_profiles import AgentProfileCatalog
        from mini_agent.delegation import DelegationManager
        from mini_agent.skills import SkillCatalog
        from mini_agent.tools.delegation import make_background_subagent_tools
        child_provider_catalog = ProviderCatalog.from_module(runtime_config)
        child_binding = child_provider_catalog.child_binding()
        def child_client(messages, **options):
            if scenario.scenario_id == "subagent-timeout" and controller.hit(
                scenario.fault["target"], evidence={"child_request": "before_provider_io"},
            ):
                injected_calls.append({"component": "subagent_llm", "source": "injected",
                                       "counted_as_real": False, "count": 1})
                raise TimeoutError("injected_subagent_timeout")
            if scenario.scenario_id == "subagent-timeout":
                raise ValueError("reliability timeout fixture permits only one injected child request")
            response = child_binding.complete(messages, **options)
            return response.message
        delegation_manager = DelegationManager(
            workspace, subagent_llm=child_client, parent_registry=registry,
            provider_catalog=child_provider_catalog, parent_state=state,
            agent_profile_catalog=AgentProfileCatalog({}, provider_catalog=child_provider_catalog),
            skill_catalog=SkillCatalog(workspace),
        )
        registry._delegation_manager = delegation_manager
        for tool in make_background_subagent_tools(state, delegation_manager):
            if tool.name in scenario.allowed_tools:
                if tool.name == "spawn_subagent" and "child_budget" in scenario.fault["parameters"]:
                    frozen_budget = scenario.fault["parameters"]["child_budget"]
                    budget_schema = tool.parameters["properties"]["budget"]
                    budget_schema["properties"] = {
                        key: budget_schema["properties"][key] for key in frozen_budget
                    }
                    budget_schema["required"] = list(frozen_budget)
                    for key, value in frozen_budget.items():
                        budget_schema["properties"][key]["enum"] = [value]
                    tool.parameters["required"].append("budget")
                    validate_spawn = tool.argument_validator
                    def validate_frozen_spawn(arguments, _validate=validate_spawn):
                        if arguments.get("budget") != frozen_budget:
                            raise ValueError("reliability timeout fixture requires its published child_budget")
                        if state.background_subagent_records():
                            raise ValueError("reliability timeout fixture permits one child investigation")
                        _validate(arguments)
                    tool.argument_validator = validate_frozen_spawn
                registry.register(tool)

    _register_declared_plan_tools(registry, state, scenario.allowed_tools)
    recovery_runtime = _register_declared_recovery_tool(registry, state, scenario.allowed_tools)
    _observe_tool_failures(registry, tool_diagnostics)

    rule_map: dict[str, Any] = {}
    for rule in scenario.permission_rules:
        block = rule_map.setdefault(rule["tool"], {})
        block[rule["pattern"]] = rule["action"]
    for name in scenario.allowed_tools:
        rule_map.setdefault(name, "deny")
    class ReliabilityPermissionGate(NonInteractivePermissionGate):
        def guard(self, tool_name: str, args: dict, *, display_context: dict | None = None):
            message = super().guard(tool_name, args, display_context=display_context)
            hit = _record_denied_write_fault(
                controller, scenario_id=scenario.scenario_id, tool_name=tool_name,
                arguments=args, message=message,
            )
            if hit:
                denied_target_hashes.add(_sha_json(args))
            return message

    gate = ReliabilityPermissionGate(PermissionPolicy(rule_map))
    executor = ToolExecutor(registry, gate=gate, on_result=state.record_tool)
    if recovery_runtime is not None:
        recovery_runtime.bind_executor(executor)
    case = {"task": scenario.task, "allowed_tools": list(scenario.allowed_tools),
            "authorized_tools": [row["tool"] for row in scenario.permission_rules if row["action"] == "allow"],
            "model_profile": None}
    trial_request = TrialRequest(
        trial_id=request["trial_id"], run_kind="live", case=case,
        workspace=str(workspace), responses=(),
    )
    client, binding, _counts = _llm_client(trial_request, workspace, registry)
    request_diagnostics = RequestDiagnostics(binding, scenario.budget["parent_tokens"])
    client = request_diagnostics.wrap_client(client)
    context = ContextManager(
        state, [{"role": "user", "content": scenario.task}],
        budget=ContextBudget(window=binding.profile.context_window),
        observability=False,
        protected_messages=_reliability_protected_messages(workspace),
        finalization_protected_messages=_reliability_protected_messages(workspace, finalization=True),
        model_binding=binding, memory_retrieval_enabled=False,
    )
    session_store = None
    session_id = None
    session_boundary = None
    if scenario.scenario_id == "crash-after-handler":
        from mini_agent.session import DurableToolBoundary, SessionStore
        session_store = SessionStore(request["session_root"])
        session_id = session_store.save(
            None, state, context, workspace_root=workspace, handoff_status="active",
        )["session_id"]
        session_boundary = DurableToolBoundary(session_store, session_id, workspace)
        executor.session_boundary = session_boundary

    class ReliabilityRuntimePolicy(ParentRuntimePolicy):
        def __init__(self):
            super().__init__()
            self.tool_budget_rejected = False
            self.token_budget_rejected = False
            self.deadline = 0.0
        def _wall_timeout(self):
            state.status = "failed"
            state.terminal_reason = "reliability_wall_timeout"
            return RuntimeDecision("finish", "reliability Agent wall budget exhausted", "agent_timeout")
        def prepare_tool_round(self, runtime, calls):
            plan = super().prepare_tool_round(runtime, calls)
            self.token_budget_rejected = False
            if not self.token_budget_rejected and runtime.tool_calls + len(calls) <= scenario.budget["tool_calls"]:
                return plan
            self.tool_budget_rejected = not self.token_budget_rejected
            budget_kind = "token" if self.token_budget_rejected else "tool"
            rejected = dict(plan.rejection_by_index)
            for index, (name, arguments) in enumerate(runtime.parsed_calls):
                rejected[index] = ExecutionResult(
                    name, arguments, "not_checked", False, "denied", 0,
                    runtime.effects[index], f"权限拒绝: reliability {budget_kind} budget exhausted",
                    f"reliability {budget_kind} budget exhausted", error_kind="budget_exhausted",
                )
            return ToolRoundPlan(serial=True, rejection_by_index=rejected)
        def before_llm(self, runtime):
            runtime._refresh_usage()
            pending_tokens = _pending_request_tokens(runtime)
            admitted = request_diagnostics.before_request(runtime, pending_tokens)
            if not admitted:
                request_diagnostics.refuse("token_limit")
            if self.deadline and time.monotonic() >= self.deadline:
                request_diagnostics.refuse("agent_timeout")
                return self._wall_timeout()
            return super().before_llm(runtime)
        def on_text(self, runtime, content):
            if self.deadline and time.monotonic() >= self.deadline:
                return self._wall_timeout()
            return super().on_text(runtime, content)
        def after_tool_round(self, runtime, calls, results):
            decision = super().after_tool_round(runtime, calls, results)
            if self.token_budget_rejected:
                state.status = "failed"
                state.terminal_reason = "reliability_token_budget_exhausted"
                return RuntimeDecision("finish", "provider usage exceeded reliability token budget", "token_limit")
            if self.tool_budget_rejected:
                if state.status not in ("blocked", "failed"):
                    state.status = "failed"
                    state.terminal_reason = "reliability_tool_budget_exhausted"
                return RuntimeDecision("finish", "reliability tool budget exhausted", "tool_limit")
            if self.deadline and time.monotonic() >= self.deadline:
                return self._wall_timeout()
            return decision

    policy = ReliabilityRuntimePolicy()
    runtime = AgentRuntime(
        llm_client=client, context=context, executor=executor, policy=policy,
        max_rounds=scenario.budget["max_rounds"], session_boundary=session_boundary,
        model_binding=binding, token_budget=scenario.budget["parent_tokens"],
    )
    started = time.monotonic()
    _write_small_json(workspace.parent / "worker-stage.json", {
        "stage": "agent", "agent_started": True,
    })
    policy.deadline = started + scenario.budget["wall_seconds"]
    stop_reason = "agent_error"
    error_kind = None
    infrastructure_error = None
    diagnostic = None
    process_handoffs = []
    try:
        agent_result = runtime.run()
        stop_reason = agent_result.stop_reason
        if scenario.scenario_id == "process-wait-timeout" and stop_reason == "awaiting_process":
            process_handoffs.append(stop_reason)
            facts = process_manager.sync_processes(state.task_id)
            state.sync_processes(facts)
            state.resume_process_wait()
            context.history.append({"role": "user", "content": "我已同步当前进程状态，请继续处理并完成清理和验证。"})
            agent_result = runtime.run()
            stop_reason = agent_result.stop_reason
        if stop_reason == "text" and state.status == "running":
            state.status = "done"
    except Exception as error:
        error_kind = type(error).__name__
        diagnostic = _exception_diagnostic(error, stage="agent", binding=binding)
        infrastructure_error = _provider_infrastructure_category(error)
        stop_reason = "agent_timeout" if isinstance(error, TimeoutError) else "agent_error"
        if state.status == "running":
            state.status = "failed"
            state.terminal_reason = (
                "reliability_agent_timeout" if stop_reason == "agent_timeout"
                else "reliability_agent_error"
            )
    duration_ms = int((time.monotonic() - started) * 1000)
    runtime._refresh_usage()
    cleanup_complete = True
    cleanup_issue = None
    if process_manager is not None:
        report = process_manager.cleanup(state.task_id)
        state.record_process_cleanup(report)
        state.sync_processes(process_manager.sync_processes(state.task_id))
        cleanup_complete = report.complete
        if not cleanup_complete:
            cleanup_issue = "process_manager_cleanup_incomplete"
    if delegation_manager is not None:
        try:
            delegation_manager.cancel(reason="reliability_trial_cleanup", interrupt=False)
            cleanup_complete = delegation_manager.wait(2) and cleanup_complete
            delegation_manager.collect_background_events(dispatch=False)
        except Exception:
            cleanup_complete = False
            cleanup_issue = cleanup_issue or "subagent_manager_cleanup_incomplete"
    if mcp_manager is not None:
        close_report = mcp_manager.close()
        cleanup_complete = bool(close_report.get("closed")) and cleanup_complete
        if not close_report.get("closed"):
            cleanup_issue = cleanup_issue or "mcp_manager_cleanup_incomplete"

    attempts = list(state.attempts)
    _write_small_json(workspace.parent / "worker-stage.json", {
        "stage": "grading", "agent_started": True, "agent_stop_reason": stop_reason,
        "agent_terminal_status": state.status,
        "fault_status": "triggered" if controller.hit_count else "not_triggered",
        "fault_events": list(controller.events), "cleanup_complete": cleanup_complete,
    })
    invariant_values: dict[str, bool] = {}
    sid = scenario.scenario_id
    if sid == "tool-handler-exception":
        failed = next((item for item in attempts if item.error_kind == "handler_exception"), None)
        failed_call_ids = set()
        for message in context.history:
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                except (TypeError, ValueError):
                    continue
                if failed and function.get("name") == failed.tool and _sha_json(arguments) == failed.arguments_hash:
                    failed_call_ids.add(call.get("id"))
        has_tool_result = bool(failed and any(
            item.get("role") == "tool" and item.get("tool_call_id") in failed_call_ids
            and "injected_handler_failure" in str(item.get("content", ""))
            for item in context.history
        ))
        invariant_values["tool-result-replayed"] = bool(failed and failed.handler_admitted and has_tool_result)
    elif sid == "permission-denied":
        denied = [item for item in attempts if item.tool == "write_file"
                  and item.permission == "denied" and not item.handler_admitted
                  and item.arguments_hash in denied_target_hashes]
        invariant_values["denied-handler-not-run"] = bool(denied)
        locked = workspace / "src" / "locked.py"
        invariant_values["no-implicit-approval"] = bool(denied) and locked.is_file() and locked.read_bytes() == locked_before
        invariant_values["permission-stops-task"] = bool(denied) and state.status == "failed" and stop_reason == "failed"
    elif sid == "verification-repair":
        evidence = state.verification_evidence
        invariant_values["verification-current-generation"] = bool(
            evidence and evidence[-1].generation_id == state.current_generation_id
            and evidence[-1].exit_code == 0
        )
        invariant_values["hidden-grader-passes"] = _grade_workspace(
            scenario, workspace, scenario_directory / "grader.json",
        ) is True
    elif sid == "process-wait-timeout":
        waits = [item for item in attempts if item.tool == "wait_process"]
        parsed_waits = []
        for item in waits:
            try:
                parsed_waits.append(json.loads(item.output_excerpt))
            except (TypeError, ValueError):
                pass
        invariant_values["timeout-not-exit"] = any(
            item.get("reason") == "still_running" and item.get("exit_code") is None
            for item in parsed_waits
        )
        invariant_values["active-process-blocks-done"] = "awaiting_process" in process_handoffs
        invariant_values["cleanup-complete"] = cleanup_complete and not state.active_process_records()
    elif sid == "mcp-disconnect":
        invariant_values["connection-not-reused"] = bool(mcp_manager and all(not item.connected for item in mcp_manager.clients))
        invariant_values["no-call-retry"] = _record_count(mcp_record, "tools/call") == 1
    elif sid == "subagent-timeout":
        records = state.background_subagent_records()
        record = records[0] if records else None
        invariant_values["failed-subagent-claimed"] = bool(
            record and record.delivery_status == "committed" and record.result_id
            and record.outcome == "timed_out"
        )
        invariant_values["usage-settled-once"] = bool(
            record and state.delegation_budget.used_llm_calls == record.usage.llm_calls
            and record.usage.llm_calls <= 1
        )

    grader_started = time.monotonic()
    static_grade = _grade_workspace(scenario, workspace, scenario_directory / "grader.json")
    public_grade = _run_public_test(workspace, public_command) if "run_shell" in scenario.allowed_tools else None
    file_grade = static_grade if public_grade is None else (static_grade is True and public_grade is True)
    if sid != "permission-denied" and (static_grade is None or public_grade is None):
        infrastructure_error = infrastructure_error or "grader_unavailable"
    invariant_rows = [
        {"invariant_id": key, "status": "passed" if invariant_values.get(key) is True
         else "failed" if key in invariant_values else "incomplete"}
        for key in scenario.invariants
    ]
    if not controller.hit_count:
        # No observation of the target seam cannot establish that its boundary
        # failed. Independent cleanup/accounting checks remain observable.
        for row in invariant_rows:
            if row["invariant_id"] not in {"cleanup-complete", "usage-settled-once"}:
                row["status"] = "incomplete"
    statuses = {item["status"] for item in invariant_rows}
    invariant_status = "passed" if statuses == {"passed"} else "failed" if "failed" in statuses else "incomplete"
    fault_status = "triggered" if controller.hit_count else "not_triggered"
    recovery_status = (
        "succeeded" if fault_status == "triggered" and state.status == "done" and file_grade is True
        else "failed" if fault_status == "triggered" else "not_triggered"
    )
    if sid == "permission-denied":
        recovery_status = "not_applicable"
        file_grade = None
    summary = {
        "status": state.status, "terminal_reason": state.terminal_reason[:200],
        "completion_evidence": {
            "artifact_correct": file_grade is True,
            "current_generation_verified": bool(state.verification_evidence and
                state._last_verified_generation == state._verification_generation and not state._verification_required),
            "model_completed": stop_reason == "text" and state.status == "done",
            "blockers": list(state.completion_blockers()),
        },
        "task_id": state.task_id, "generation": state.current_generation_id,
        "attempts": [{"tool": item.tool, "outcome": item.outcome,
                      "permission": item.permission, "handler_admitted": item.handler_admitted,
                      "error_kind": item.error_kind, "attempt_id": item.attempt_id,
                      "arguments": item.redacted_arguments} for item in attempts[:64]],
        "verification": [{"outcome": item.outcome, "exit_code": item.exit_code,
                          "generation_id": item.generation_id} for item in state.verification_evidence[:16]],
        "crash_issue_count": len(state.crash_issues),
        "model_binding_ref": binding.reference.to_dict(),
        "delegation_records": [asdict(item) for item in state.background_subagent_records()[:8]],
        "injected_calls": injected_calls,
        "agent_error_kind": error_kind,
        "provider_error_category": infrastructure_error,
        "initial_public_test_passed": initial_public_test,
        "diagnostic": diagnostic,
        "pre_injection_stop_reason": stop_reason if not controller.hit_count else None,
        "tool_schema_sha256": _sha_json(registry.schemas()),
        "tool_diagnostics": tool_diagnostics,
    }
    return {
        "fault_status": fault_status, "fault_events": list(controller.events),
        "invariant_status": invariant_status, "invariants": invariant_rows,
        "recovery_status": recovery_status, "recovery_steps": [], "grader_passed": file_grade,
        "agent_stop_reason": stop_reason, "agent_terminal_status": state.status,
        "cleanup_complete": cleanup_complete, "cleanup_issue": cleanup_issue,
        "infrastructure_error": infrastructure_error, "source_consistent": True,
        "phases": {"agent": {"duration_ms": duration_ms, "llm_calls": runtime.llm_calls,
                              "tool_calls": runtime.tool_calls, "input_tokens": runtime.input_tokens,
                              "output_tokens": runtime.output_tokens,
                              "request_diagnostics": request_diagnostics.snapshot(runtime),
                              "token_accounting": runtime.token_accounting,
                              "injected_calls": injected_calls,
                              "model_binding_ref": binding.reference.to_dict()},
                   "grader": {"duration_ms": int((time.monotonic() - grader_started) * 1000)}},
        "artifacts": {"evidence": []}, "manual_review": [], "feedback_events": [],
        "state_summary": summary,
        "trace_summary": {"trace_events": len(state.trace_events), "sha256": _sha_json(summary),
                          "events": [asdict(event) for event in state.trace_events[:128]]},
        "diff_patch": "",
    }


def _grade_workspace(scenario: Any, workspace: Path, grader_contract_path: Path | None = None) -> bool | None:
    sid = scenario.scenario_id if hasattr(scenario, "scenario_id") else scenario["scenario_id"]
    if grader_contract_path is None:
        return None
    try:
        grader_contract = json.loads(grader_contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if "task-transform-doubles" not in grader_contract.get("checks", []):
        return None
    expected = {
        "tool-handler-exception": "src/example.py",
        "permission-denied": "src/fallback.py",
        "verification-repair": "src/example.py",
        "process-wait-timeout": "src/example.py",
        "crash-after-handler": "src/example.py",
        "mcp-disconnect": "src/example.py",
        "subagent-timeout": "src/example.py",
    }.get(sid)
    if expected is None:
        return None
    path = workspace / expected
    if not path.exists() or not path.is_file():
        return False
    # The same behavior oracle is used for calibration and post-Agent grading.
    # Run it in a separate process, accepting equivalent implementations.
    grader = Path(__file__).with_name("reliability_grader.py")
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(grader), str(workspace.resolve()), expected],
                cwd=workspace, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=30, check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            if result.returncode != 0 or output.tell() > 4096:
                return None
            output.seek(0)
            raw = json.loads(output.read().decode("utf-8"))
            return raw["passed"] if isinstance(raw.get("passed"), bool) else None
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        return None


def _resume_live_crash(request: dict[str, Any]) -> dict[str, Any]:
    """Claim and continue a crash trial in the runner's fresh Python process."""
    from mini_agent.agent import ParentRuntimePolicy
    from mini_agent.context import ContextBudget
    from mini_agent.permission import PermissionPolicy
    from mini_agent.resume import prepare_resume
    from mini_agent.runtime import AgentRuntime, RuntimeDecision, ToolRoundPlan
    from mini_agent.session import DurableToolBoundary, SessionStore
    from mini_agent.tools.base import ExecutionResult, Tool, ToolExecutor
    from mini_agent.tools.plan import (
        make_begin_plan_tool, make_commit_plan_tool, make_update_plan_progress_tool,
    )
    from mini_agent.user_actions import resolve_crash_issue, script_digest
    from mini_agent.evaluation.worker import NonInteractivePermissionGate, _build_registry

    scenario = validate_scenario(request["scenario"], expected_id="crash-after-handler")
    workspace = Path(request["workspace"]).resolve(strict=True)
    scenario_directory = Path(request["scenario_directory"]).resolve(strict=True)
    metadata_path = Path(request["crash_meta_path"])
    if metadata_path.is_symlink() or not metadata_path.is_file() or metadata_path.stat().st_size > 64 * 1024:
        raise ValueError("crash_metadata_missing_or_invalid")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    session_id = metadata.get("session_id")
    usage_before = metadata.get("pre_crash_usage")
    if not isinstance(session_id, str) or not isinstance(usage_before, dict):
        raise ValueError("crash_metadata_contract_invalid")
    store = SessionStore(request["session_root"])
    source_path = store.path_for(session_id)
    source_before = source_path.read_bytes()
    candidate = prepare_resume(store, session_id, workspace)
    if candidate.recovery_mode != "crash_recovery":
        raise ValueError("crash_source_did_not_enter_recovery")
    resumed = candidate.claim()
    state = resumed.state
    context = resumed.context
    claimed_boundary = store.load(resumed.session_id).get("tool_boundary", {})
    claimed_pending_calls = list(claimed_boundary.get("calls", []))
    uncertain_pending_call = any(
        item.get("status") == "committed"
        and item.get("handler_admitted") is True
        and item.get("result", {}).get("outcome") == "uncertain"
        for item in claimed_pending_calls
    )
    plan_revisions_before_recovery = len(state.plan_revisions)
    binding = context.model_binding
    if (binding is None or binding.reference.to_dict() != request.get("model_binding_ref")
            or binding.reference.to_dict() != usage_before.get("binding_ref")):
        raise ValueError("crash_resume_model_binding_changed")

    prior_mcp = getattr(resumed.registry, "_mcp_manager", None)
    if prior_mcp is not None:
        prior_mcp.close()
    prior_delegation = getattr(resumed.registry, "_delegation_manager", None)
    if prior_delegation is not None:
        prior_delegation.cancel(reason="reliability_resume_rebuild", interrupt=False)
        if not prior_delegation.wait(1):
            raise RuntimeError("unexpected_child_cleanup_incomplete")
    if resumed.process_manager is not None:
        cleanup = resumed.process_manager.cleanup(state.task_id)
        state.record_process_cleanup(cleanup)
        if not cleanup.complete:
            raise RuntimeError("unexpected_process_cleanup_incomplete")

    registry = _build_registry({"allowed_tools": [
        name for name in scenario.allowed_tools
        if name in {"read_file", "list_dir", "grep", "write_file", "edit_file", "calculate"}
    ]}, workspace)
    from mini_agent.tools.shell import run_shell_tool
    public_command = "python -m unittest discover -s tests"
    def bounded_shell(command: str, purpose: str = "execution"):
        if command != public_command or purpose != "verification":
            return "[exit=126] reliability fixture permits only its public verification command"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, timeout=30, check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            return f"[exit={result.returncode}] {result.stdout[-4000:]}"
        except subprocess.TimeoutExpired:
            return "[timeout] public verification exceeded 30 seconds"
    registry.register(Tool(
        "run_shell", run_shell_tool.description, deepcopy(run_shell_tool.parameters),
        bounded_shell, effect_class="possible",
    ))
    registry.register(make_begin_plan_tool(state))
    registry.register(make_commit_plan_tool(state))
    registry.register(make_update_plan_progress_tool(state))
    rule_map: dict[str, Any] = {}
    for rule in scenario.permission_rules:
        rule_map.setdefault(rule["tool"], {})[rule["pattern"]] = rule["action"]
    for name in scenario.allowed_tools:
        rule_map.setdefault(name, "deny")
    gate = NonInteractivePermissionGate(PermissionPolicy(rule_map))
    executor = ToolExecutor(registry, gate=gate, on_result=state.record_tool)
    executor.session_boundary = DurableToolBoundary(store, resumed.session_id, workspace)
    context.model_binding = binding
    context.skill_catalog = None
    context.memory_retriever = None
    context.memory_retrieval_enabled = False
    # Resume rebuilds the production prompt from ancestor AGENTS.md. Evaluation
    # must rebuild its own protected instructions just as the initial worker does.
    context.protected_messages = _reliability_protected_messages(workspace, recovery=True)
    context.finalization_protected_messages = _reliability_protected_messages(
        workspace, recovery=True, finalization=True)
    tool_diagnostics: list[dict] = []
    _observe_tool_failures(registry, tool_diagnostics)
    raw_feedback = (scenario_directory / "feedback.json").read_bytes()
    feedback = json.loads(raw_feedback.decode("utf-8"))
    digest = script_digest(raw_feedback)
    feedback_events: list[dict[str, Any]] = []
    issues = list(state.unresolved_crash_issues)
    investigation_attempts: list[str] = []
    for issue in issues:
        investigation = resolve_crash_issue(
            state, issue.issue_id, "investigate", feedback[0]["feedback"],
            source="simulated_user", metadata={
                "script_sha256": digest, "trigger": feedback[0]["when"],
                "issue_id": issue.issue_id, "accepted": True,
            },
        )
        feedback_events.append({
            "source": investigation.source, "script_sha256": digest,
            "trigger": feedback[0]["when"], "issue_id": issue.issue_id,
            "decision": "investigate", "accepted": True,
        })
        observation = executor.execute_result(
            "read_file", {"path": "src/example.py"}, state=state,
        )
        attempt = state.record_execution_result(observation)
        investigation_attempts.append(attempt.attempt_id)
        decision = resolve_crash_issue(
            state, issue.issue_id, "continue", feedback[1]["feedback"],
            source="simulated_user", metadata={
                "script_sha256": digest, "trigger": feedback[1]["when"],
                "issue_id": issue.issue_id, "accepted": True,
            },
        )
        feedback_events.append({
            "source": decision.source, "script_sha256": digest,
            "trigger": feedback[1]["when"], "issue_id": issue.issue_id,
            "decision": "continue", "accepted": True,
            "investigation_attempt_id": attempt.attempt_id,
        })
    context.history.append({
        "role": "user",
        "content": "模拟用户已依据冻结反馈逐项处理 crash issue。请继续调查，提交新 revision 并重新验证。",
    })
    prior_rounds = int(usage_before.get("rounds", 0))
    prior_calls = int(usage_before.get("tool_calls", 0))
    prior_tokens = int(usage_before.get("input_tokens", 0)) + int(usage_before.get("output_tokens", 0))
    remaining_rounds = max(0, scenario.budget["max_rounds"] - prior_rounds)
    remaining_calls = max(0, scenario.budget["tool_calls"] - prior_calls)
    request_diagnostics = RequestDiagnostics(
        binding, scenario.budget["parent_tokens"], prior_tokens=prior_tokens,
        prior_requests=usage_before.get("request_diagnostics", {}).get("requests", []),
    )
    remaining_wall = max(
        0.0, scenario.budget["wall_seconds"] - int(usage_before.get("elapsed_wall_ms", 0)) / 1000,
    )

    class RecoveryPolicy(ParentRuntimePolicy):
        def __init__(self):
            super().__init__()
            self.tool_budget_rejected = False
            self.token_budget_rejected = False
            self.deadline = time.monotonic() + remaining_wall
        def _fail_budget(self, reason: str, stop_reason: str):
            state.status = "failed"
            state.terminal_reason = reason
            return RuntimeDecision("finish", reason.replace("_", " "), stop_reason)
        def prepare_tool_round(self, runtime, calls):
            plan = super().prepare_tool_round(runtime, calls)
            self.token_budget_rejected = False
            if not self.token_budget_rejected and runtime.tool_calls + len(calls) <= remaining_calls:
                return plan
            self.tool_budget_rejected = not self.token_budget_rejected
            budget_kind = "token" if self.token_budget_rejected else "tool"
            rejected = dict(plan.rejection_by_index)
            for index, (name, arguments) in enumerate(runtime.parsed_calls):
                rejected[index] = ExecutionResult(
                    name, arguments, "not_checked", False, "denied", 0,
                    runtime.effects[index], f"权限拒绝: recovery {budget_kind} budget exhausted",
                    f"recovery {budget_kind} budget exhausted", error_kind="budget_exhausted",
                )
            return ToolRoundPlan(serial=True, rejection_by_index=rejected)
        def before_llm(self, runtime):
            runtime._refresh_usage()
            pending_tokens = _pending_request_tokens(runtime)
            admitted = request_diagnostics.before_request(runtime, pending_tokens)
            if not admitted:
                request_diagnostics.refuse("token_limit")
            if remaining_rounds == 0 or runtime.rounds >= remaining_rounds:
                request_diagnostics.refuse("round_limit")
                return self._fail_budget("reliability_total_round_budget_exhausted", "round_limit")
            if remaining_calls == 0:
                request_diagnostics.refuse("tool_limit")
                return self._fail_budget("reliability_total_tool_budget_exhausted", "tool_limit")
            if time.monotonic() >= self.deadline:
                request_diagnostics.refuse("agent_timeout")
                return self._fail_budget("reliability_total_wall_budget_exhausted", "agent_timeout")
            return super().before_llm(runtime)
        def on_text(self, runtime, content):
            if time.monotonic() >= self.deadline:
                return self._fail_budget("reliability_total_wall_budget_exhausted", "agent_timeout")
            return super().on_text(runtime, content)
        def after_tool_round(self, runtime, calls, results):
            result = super().after_tool_round(runtime, calls, results)
            if self.token_budget_rejected:
                return self._fail_budget("reliability_total_token_budget_exhausted", "token_limit")
            if self.tool_budget_rejected:
                state.status = "failed"
                state.terminal_reason = "reliability_total_tool_budget_exhausted"
                return RuntimeDecision("finish", "reliability total tool budget exhausted", "tool_limit")
            if time.monotonic() >= self.deadline:
                return self._fail_budget("reliability_total_wall_budget_exhausted", "agent_timeout")
            return result

    client = request_diagnostics.wrap_client(binding.complete)
    runtime = AgentRuntime(
        llm_client=client, context=context, executor=executor, policy=RecoveryPolicy(),
        max_rounds=max(1, remaining_rounds), session_boundary=executor.session_boundary,
        model_binding=binding, token_budget=scenario.budget["parent_tokens"],
    )
    started = time.monotonic()
    stop_reason = "agent_error"
    error_kind = None
    diagnostic = None
    infrastructure_error = None
    try:
        agent_result = runtime.run()
        stop_reason = agent_result.stop_reason
        if stop_reason == "text" and state.status == "running":
            state.status = "done"
    except Exception as error:
        error_kind = type(error).__name__
        diagnostic = _exception_diagnostic(error, stage="recovery_agent", binding=binding)
        infrastructure_error = _provider_infrastructure_category(error)
        stop_reason = "agent_timeout" if isinstance(error, TimeoutError) else "agent_error"
        if state.status == "running":
            state.status = "failed"
            state.terminal_reason = (
                "reliability_recovery_agent_timeout" if stop_reason == "agent_timeout"
                else "reliability_recovery_agent_error"
            )
    duration_ms = int((time.monotonic() - started) * 1000)
    runtime._refresh_usage()
    source_unchanged = source_path.read_bytes() == source_before
    uncertain_not_replayed = uncertain_pending_call
    new_revision = (
        len(state.plan_revisions) > plan_revisions_before_recovery
        and state.planning_state.phase in {"executing", "complete"}
    )
    verification = bool(
        state.verification_evidence
        and state.verification_evidence[-1].generation_id == state.current_generation_id
        and state.verification_evidence[-1].exit_code == 0
    )
    static_grade = _grade_workspace(scenario, workspace, scenario_directory / "grader.json")
    public_grade = _run_public_test(workspace, public_command)
    grader_passed = static_grade is True and public_grade is True
    if static_grade is None or public_grade is None:
        infrastructure_error = infrastructure_error or "grader_unavailable"
    invariant_values = {
        "side-effect-once": metadata.get("side_effect_count") == 1,
        "admitted-call-not-replayed": uncertain_not_replayed,
        "source-session-read-only": source_unchanged,
    }
    invariant_rows = [
        {"invariant_id": key, "status": "passed" if invariant_values.get(key) else "failed"}
        for key in scenario.invariants
    ]
    recovery_steps = [
        {"step": "claim_new_session", "status": "succeeded", "process": "new_python_process"},
        {"step": "inspect_evidence", "status": "succeeded" if uncertain_not_replayed else "failed"},
        {"step": "resolve_issues", "status": "succeeded" if len(feedback_events) == len(issues) * 2 else "failed"},
        {"step": "replan", "status": "succeeded" if new_revision else "failed"},
        {"step": "reauthorize", "status": "succeeded" if any(item.permission == "allowed" for item in state.attempts) else "failed"},
        {"step": "verify_current_generation", "status": "succeeded" if verification else "failed"},
    ]
    all_recovery = all(item["status"] == "succeeded" for item in recovery_steps)
    summary = {
        "completion_evidence": {
            "artifact_correct": grader_passed is True,
            "current_generation_verified": verification,
            "model_completed": stop_reason == "text" and state.status == "done",
            "blockers": list(state.completion_blockers()),
        },
        "status": state.status, "terminal_reason": state.terminal_reason[:200],
        "agent_error_kind": error_kind,
        "diagnostic": diagnostic,
        "task_id": state.task_id, "source_session_id": session_id,
        "tool_diagnostics": tool_diagnostics,
        "derived_session_id": resumed.session_id, "source_session_unchanged": source_unchanged,
        "crash_issue_count": len(issues), "crash_decisions": len(state.crash_decisions),
        "side_effect_count": metadata.get("side_effect_count"),
        "pending_call_uncertain": uncertain_not_replayed,
        "plan_revision_count": len(state.plan_revisions),
        "verification_generation": state.verification_evidence[-1].generation_id if state.verification_evidence else None,
        "attempts": [{"tool": item.tool, "outcome": item.outcome,
                      "permission": item.permission, "handler_admitted": item.handler_admitted,
                      "error_kind": item.error_kind} for item in state.attempts[:64]],
    }
    return {
        "fault_status": "triggered", "fault_events": metadata.get("fault_events", []),
        "invariant_status": "passed" if all(invariant_values.values()) else "failed",
        "invariants": invariant_rows,
        "recovery_status": "succeeded" if all_recovery and state.status == "done" and grader_passed
        else "failed",
        "recovery_steps": recovery_steps, "grader_passed": grader_passed,
        "agent_stop_reason": stop_reason, "agent_terminal_status": state.status,
        "cleanup_complete": True, "cleanup_issue": None,
        "infrastructure_error": infrastructure_error, "source_consistent": source_unchanged,
        "phases": {
            "pre_crash": {k: usage_before.get(k) for k in (
                "rounds", "llm_calls", "tool_calls", "input_tokens", "output_tokens", "token_accounting", "request_diagnostics",
            )},
            "recovery": {"duration_ms": duration_ms, "llm_calls": runtime.llm_calls,
                         "tool_calls": runtime.tool_calls, "input_tokens": runtime.input_tokens,
                         "output_tokens": runtime.output_tokens,
                         "request_diagnostics": request_diagnostics.snapshot(runtime),
                         "remaining_wall_seconds": remaining_wall,
                         "token_accounting": runtime.token_accounting,
                         "model_binding_ref": binding.reference.to_dict()},
            "grader": {"duration_ms": 0},
        },
        "artifacts": {"evidence": []}, "manual_review": [],
        "feedback_events": feedback_events, "state_summary": summary,
        "trace_summary": {"trace_events": len(state.trace_events), "sha256": _sha_json(summary)},
        "diff_patch": "",
    }


def run_request(raw: Any, *, resume_live_crash: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("format") != "mini_agent.reliability_request" or raw.get("schema_version") != 1:
        raise ValueError("reliability request format invalid")
    scenario = validate_scenario(raw.get("scenario"))
    workspace = Path(raw["workspace"]).resolve(strict=True)
    scenario_directory = Path(raw["scenario_directory"]).resolve(strict=True)
    run_kind = raw.get("run_kind")
    if run_kind not in {"fixture", "live"}:
        raise ValueError("run_kind invalid")
    started = _now()
    controller = FaultController(scenario.fault)
    if resume_live_crash:
        if run_kind != "live" or scenario.scenario_id != "crash-after-handler":
            raise ValueError("live crash recovery worker contract invalid")
        result = _resume_live_crash(raw)
    else:
        result = _offline_probe(scenario.to_dict(), workspace, scenario_directory, controller) if run_kind == "fixture" else _run_live(raw)
    evidence_root = workspace.parent
    evidence: list[dict[str, Any]] = []
    for name, value in (
        ("fault-events.json", result.get("fault_events", [])),
        ("state-summary.json", result.get("state_summary", {})),
        ("trace-summary.json", result.get("trace_summary", {})),
        ("recovery-steps.json", result.get("recovery_steps", [])),
        ("feedback-events.json", result.get("feedback_events", [])),
    ):
        evidence.append(write_evidence(evidence_root, name, value))
    result.update({
        "format": "mini_agent.reliability", "schema_version": 1,
        "trial_id": raw["trial_id"], "suite_id": raw["suite_id"],
        "suite_version": raw["suite_version"], "suite_sha256": raw["suite_sha256"],
        "scenario_id": scenario.scenario_id, "scenario_sha256": raw["scenario_sha256"],
        "run_kind": run_kind, "variant": raw.get("variant", {}),
        "repetition": raw["repetition"], "status": "completed",
        "started_at": started, "ended_at": _now(),
    })
    result["artifacts"] = {"evidence": evidence}
    return result


def _recover_subagent_session(store_root: str, session_id: str, workspace_root: str,
                              feedback_path: str) -> dict[str, Any]:
    """Recovery helper intentionally executed in a fresh Python process."""
    from mini_agent.context import ContextManager
    from mini_agent.resume import prepare_resume
    from mini_agent.session import SessionStore
    from mini_agent.tools.base import ExecutionResult
    from mini_agent.user_actions import resolve_crash_issue, script_digest

    store = SessionStore(store_root)
    source_path = store.path_for(session_id)
    source_before = source_path.read_bytes()
    feedback_raw = Path(feedback_path).read_bytes()
    feedback = json.loads(feedback_raw.decode("utf-8"))
    if not isinstance(feedback, list) or len(feedback) != 2:
        raise ValueError("frozen_feedback_contract_invalid")
    resumed = prepare_resume(store, session_id, workspace_root).claim()
    state = resumed.state
    records = state.background_subagent_records()
    if not records:
        raise ValueError("recovered_background_record_missing")
    record = records[0]
    issues = list(state.unresolved_crash_issues)
    actions: list[dict[str, Any]] = []
    digest = script_digest(feedback_raw)
    for issue in issues:
        investigate = resolve_crash_issue(
            state, issue.issue_id, "investigate", feedback[0]["feedback"],
            source="simulated_user", metadata={
                "script_sha256": digest, "trigger": feedback[0]["when"],
                "issue_id": issue.issue_id, "accepted": True,
            },
        )
        actions.append({
            "source": investigate.source, "script_sha256": digest,
            "trigger": feedback[0]["when"], "issue_id": issue.issue_id,
            "decision": "investigate", "status": investigate.status,
        })
        observation = ExecutionResult(
            "read_file", {"path": "src/example.py"}, "allowed", True,
            "succeeded", 0, "none", "bounded recovery fixture observation", "observed",
        )
        attempt = state.record_execution_result(observation)
        continued = resolve_crash_issue(
            state, issue.issue_id, "continue", feedback[1]["feedback"],
            source="simulated_user", metadata={
                "script_sha256": digest, "trigger": feedback[1]["when"],
                "issue_id": issue.issue_id, "accepted": True,
            },
        )
        actions.append({
            "source": continued.source, "script_sha256": digest,
            "trigger": feedback[1]["when"], "issue_id": issue.issue_id,
            "decision": "continue", "status": continued.status,
            "investigation_attempt_id": attempt.attempt_id,
        })
    source_unchanged = source_path.read_bytes() == source_before
    return {
        "delivery_status": record.delivery_status, "result_id": record.result_id,
        "derived_session_id": resumed.session_id,
        "used_llm_calls": state.delegation_budget.used_llm_calls,
        "reserved_llm_calls": record.reserved_usage.llm_calls,
        "issue_count": len(issues),
        "resolved_issue_count": sum(item.status == "continued" for item in state.crash_issues),
        "user_action_source": actions[0]["source"] if actions else None,
        "user_actions": actions,
        "source_unchanged": source_unchanged,
        "context_has_pending_attempt": bool(resumed.context.history),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] == "--resume-live-crash":
        try:
            request_path = Path(args[1])
            if request_path.stat().st_size > 512 * 1024:
                raise ValueError("request_too_large")
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result = run_request(request, resume_live_crash=True)
            rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(rendered.encode("utf-8")) > 256 * 1024:
                raise ValueError("result_too_large")
            sys.stdout.write(rendered + "\n")
            sys.stdout.flush()
            return 0
        except BaseException as error:
            sys.stdout.write(json.dumps({"infrastructure_error": type(error).__name__,
                                        "diagnostic": _exception_diagnostic(error, stage="resume_live_crash")},
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 1
    if len(args) == 7 and args[0] == "--recover-crash":
        try:
            result = _recover_crash_session(
                args[1], args[2], args[3], args[4], args[5], args[6],
            )
            sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 0
        except BaseException as error:
            sys.stdout.write(json.dumps({"infrastructure_error": type(error).__name__,
                                        "diagnostic": _exception_diagnostic(error, stage="recover_crash")},
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 1
    if len(args) == 5 and args[0] == "--recover-subagent":
        try:
            result = _recover_subagent_session(args[1], args[2], args[3], args[4])
            sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 0
        except BaseException as error:
            sys.stdout.write(json.dumps({"infrastructure_error": type(error).__name__,
                                        "diagnostic": _exception_diagnostic(error, stage="recover_subagent")},
                                        separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 1
    if len(args) != 1:
        return 2
    try:
        request_path = Path(args[0])
        if request_path.stat().st_size > 512 * 1024:
            raise ValueError("request_too_large")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = run_request(request)
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > 256 * 1024:
            raise ValueError("result_too_large")
        sys.stdout.write(rendered + "\n")
        sys.stdout.flush()
        return 0
    except BaseException as error:
        # Exception text may include local credentials, endpoints, or paths.
        safe = {"infrastructure_error": type(error).__name__,
                "diagnostic": _exception_diagnostic(error, stage="worker")}
        sys.stdout.write(json.dumps(safe, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_request"]
