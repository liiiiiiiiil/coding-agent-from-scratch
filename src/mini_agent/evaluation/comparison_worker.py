"""External adapter that runs the target checkout's canonical AgentRuntime.

This file is launched as a script while ``PYTHONPATH`` points only at the
selected source checkout. It deliberately imports all runtime code from that
checkout and keeps its own output free of prompts, tool bodies, and credentials.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import socket
import ssl
import http.client
from pathlib import Path
import sys
import time
import uuid
from typing import Any


MAX_WORKER_RESULT_BYTES = 64 * 1024


def _error_diagnostic(error: BaseException) -> dict[str, Any]:
    """Read only typed, content-free facts from a bounded exception chain."""
    known = (
        (ssl.SSLCertVerificationError, "tls_certificate", "SSLCertVerificationError"),
        (socket.gaierror, "dns", "gaierror"),
        (ConnectionRefusedError, "connection_refused", "ConnectionRefusedError"),
        (TimeoutError, "timeout", "TimeoutError"),
        (ssl.SSLError, "tls", "SSLError"),
        (ConnectionResetError, "connection_reset", "ConnectionResetError"),
        (http.client.RemoteDisconnected, "remote_disconnect", "RemoteDisconnected"),
        (http.client.HTTPException, "http_transport", "HTTPException"),
        (OSError, "os_error", "OSError"),
    )
    diagnostic = {"category": "unknown", "cause_type": "unknown", "errno": None, "tls_verify_code": None}
    current, seen = error, set()
    for _ in range(4):
        if id(current) in seen:
            break
        seen.add(id(current))
        for cls, category, name in known:
            if isinstance(current, cls):
                diagnostic = {"category": category, "cause_type": name, "errno": None, "tls_verify_code": None}
                number = getattr(current, "errno", None)
                if type(number) is int and -(2 ** 31) <= number < 2 ** 31:
                    diagnostic["errno"] = number
                code = getattr(current, "verify_code", None)
                if isinstance(current, ssl.SSLCertVerificationError) and type(code) is int and 0 <= code < 2 ** 31:
                    diagnostic["tls_verify_code"] = code
                break
        following = current.__cause__
        if following is None and not current.__suppress_context__:
            following = current.__context__
        if not isinstance(following, BaseException):
            break
        current = following
    return diagnostic


def _hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    except Exception:
        raw = repr(type(value)).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _memory_seed_digest(items: list[dict[str, Any]]) -> str:
    normalized = []
    for item in items:
        semantic = {
            "case_id": item["case_id"], "title": item["title"],
            "body": item["body"], "tags": list(item["tags"]), "source": item["source"],
        }
        digest = hashlib.sha256(json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        normalized.append({"case_id": item["case_id"], "semantic_sha256": digest})
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _state_fact_hash(state: Any) -> str:
    snapshot = state.snapshot()
    facts = {
        "generation": snapshot.get("current_generation_id"),
        "plan_revisions": snapshot.get("plan_revisions"),
        "plan_progress_history": snapshot.get("plan_progress_history"),
        "verification_evidence": snapshot.get("verification_evidence"),
        "verification_required": snapshot.get("verification_required"),
        "status": snapshot.get("status"),
    }
    return _hash(facts)


def _normalize_arguments(arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    result = deepcopy(arguments)

    def visit(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {str(child_key): visit(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [visit(child, key) for child in value]
        if isinstance(value, str) and key == "path":
            candidate = Path(value)
            if candidate.is_absolute():
                try:
                    return candidate.resolve(strict=False).relative_to(workspace).as_posix()
                except (OSError, ValueError):
                    return "<outside-workspace>"
        return value

    return visit(result)


class _Observations:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.outcomes = {"succeeded": 0, "failed": 0, "denied": 0, "invalid": 0, "timeout": 0}
        self.permission_denials = 0
        self.invalid_repeats = 0
        self._stage_results: set[str] = set()
        self._stage_calls: dict[str, str] = {}
        self._state_facts: str | None = None

    def observe_round(self, runtime: Any, _calls: Any, results: tuple[Any, ...]) -> None:
        state_hash = _state_fact_hash(runtime.context.state)
        if self._state_facts is not None and state_hash != self._state_facts:
            self._stage_results.clear()
            self._stage_calls.clear()
        self._state_facts = state_hash
        for execution in results:
            outcome = getattr(execution, "outcome", "failed")
            if outcome not in self.outcomes:
                outcome = "failed"
            self.outcomes[outcome] += 1
            if getattr(execution, "permission", None) == "denied":
                self.permission_denials += 1
            args = _normalize_arguments(getattr(execution, "arguments", {}), self.workspace)
            call_key = _hash({"tool": getattr(execution, "tool", "unknown"), "arguments": args})
            result_key = _hash({
                "tool": getattr(execution, "tool", "unknown"),
                "outcome": outcome,
                "output": getattr(execution, "output", None),
                "error_kind": getattr(execution, "error_kind", None),
                "exit_code": getattr(execution, "exit_code", None),
            })
            if result_key not in self._stage_results:
                # A distinct structured observation starts a new progress stage.
                self._stage_results = {result_key}
                self._stage_calls = {}
            elif self._stage_calls.get(call_key) == result_key:
                self.invalid_repeats += 1
            self._stage_calls[call_key] = result_key


def _load_live_binding(profile_name: str):
    supplied = os.environ.get("MINI_AGENT_COMPARISON_BINDING_JSON")
    if not supplied or len(supplied.encode("utf-8")) > 64 * 1024:
        raise ValueError("comparison_binding_unavailable")
    raw = json.loads(supplied)
    if not isinstance(raw, dict) or set(raw) != {"providers", "profiles", "profile", "parent_profile"}:
        raise ValueError("comparison_binding_shape_invalid")
    if raw["profile"] != profile_name or raw["parent_profile"] != profile_name:
        raise ValueError("comparison_binding_profile_mismatch")
    from mini_agent.providers.catalog import ProviderCatalog
    catalog = ProviderCatalog.from_settings(
        raw["providers"], raw["profiles"], parent_profile=raw["parent_profile"],
        subagent_allowed_profiles=(raw["profile"],),
    )
    return catalog.bind(raw["profile"])


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != {
        "schema_version", "trial_id", "case", "workspace", "run_kind", "responses",
        "memory_retrieval_enabled", "memory_dir", "memory_seeds", "memory_material_sha256",
        "model_profile", "max_total_tokens", "started_marker",
    }:
        raise ValueError("comparison_request_shape_invalid")
    if request["schema_version"] != 1 or request["run_kind"] not in {"fixture", "live"}:
        raise ValueError("comparison_request_schema_invalid")
    if type(request["memory_retrieval_enabled"]) is not bool:
        raise ValueError("comparison_memory_flag_invalid")
    if request["max_total_tokens"] != 64000:
        raise ValueError("comparison_token_budget_invalid")
    trial_id = request["trial_id"]
    if not isinstance(trial_id, str) or len(trial_id) > 64:
        raise ValueError("comparison_trial_id_invalid")
    workspace = Path(request["workspace"]).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("comparison_workspace_invalid")

    from mini_agent.agent import ParentRuntimePolicy
    from mini_agent.context import ContextBudget, ContextManager
    from mini_agent.evaluation.worker import (
        EvaluationRuntimePolicy, NonInteractivePermissionGate, _build_registry,
        _evaluation_system_prompt,
    )
    from mini_agent.memory import MemoryStore
    from mini_agent.retrieval import MemoryRetriever
    from mini_agent.permission import DENY, PermissionPolicy
    from mini_agent.runtime import AgentRuntime
    from mini_agent.state import AgentState
    from mini_agent.tools.base import ToolExecutor

    seeds = request["memory_seeds"]
    if not isinstance(seeds, list) or len(seeds) > 16 or _memory_seed_digest(seeds) != request["memory_material_sha256"]:
        raise ValueError("comparison_memory_material_invalid")
    memory_store = MemoryStore(workspace_root=workspace, memory_dir=request["memory_dir"])
    for seed in seeds:
        if not isinstance(seed, dict) or set(seed) != {"case_id", "title", "body", "tags", "source"}:
            raise ValueError("comparison_memory_seed_shape_invalid")
        memory_store.remember(seed["title"], seed["body"], seed["tags"], seed["source"])

    case = request["case"]
    state = AgentState()
    state.begin_task(case["task"])
    registry = _build_registry(case, workspace)
    allowed = tuple(case["allowed_tools"])
    authorized = set(case["authorized_tools"])
    rules = {name: ("allow" if name in authorized else DENY) for name in allowed}
    gate = NonInteractivePermissionGate(PermissionPolicy(rules))
    executor = ToolExecutor(registry, gate=gate)
    profile_name = request["model_profile"]
    binding = _load_live_binding(profile_name) if request["run_kind"] == "live" else None
    responses = list(request["responses"])
    response_index = 0
    response_count = 0

    def client(messages, *, include_tools=True, tool_registry=None, **options):
        nonlocal response_index, response_count
        if binding is None:
            if response_index >= len(responses):
                raise RuntimeError("fixture_response_exhausted")
            response = deepcopy(responses[response_index])
            response_index += 1
            response_count += 1
            return response
        response = binding.complete(
            messages, include_tools=include_tools,
            tool_registry=tool_registry or registry, stream_output=False,
            timeout=options.get("timeout"), max_output_tokens=options.get("max_output_tokens"),
            strict_tool_calls=options.get("strict_tool_calls", True),
        )
        response_count += 1
        return response

    events = {"retrieval_attempts": 0, "retrieval_failures": []}

    def observe_context(event):
        if event.kind == "memory_retrieved":
            events["retrieval_attempts"] += 1
        elif event.kind == "memory_retrieval_failed":
            events["retrieval_attempts"] += 1
            kind = event.details.get("error_type")
            if isinstance(kind, str) and len(kind) <= 120:
                events["retrieval_failures"].append(kind)

    context = ContextManager(
        state, [{"role": "user", "content": case["task"]}],
        budget=ContextBudget(window=(binding.profile.context_window if binding else 128_000)),
        summarizer=(None if binding else lambda _messages: ""),
        observability=True, observer=observe_context,
        protected_messages=[{
            "role": "system",
            "content": _evaluation_system_prompt(workspace, allowed),
        }],
        model_binding=binding,
        memory_retriever=MemoryRetriever(memory_store),
        memory_retrieval_enabled=request["memory_retrieval_enabled"],
        memory_retrieval_limit=4,
        memory_retrieval_max_chars=2400,
    )
    observations = _Observations(workspace)

    class ComparisonRuntimePolicy(EvaluationRuntimePolicy):
        def after_tool_round(self, runtime, calls, results):
            observations.observe_round(runtime, calls, results)
            return super().after_tool_round(runtime, calls, results)

    runtime = AgentRuntime(
        llm_client=client, context=context, executor=executor,
        policy=ComparisonRuntimePolicy(), max_rounds=case["max_rounds"],
        model_binding=binding, token_budget=64000,
    )
    started = time.monotonic()
    marker = Path(request["started_marker"])
    marker.write_text("started\n", encoding="ascii")
    os.chmod(marker, 0o600)
    runtime_error = None
    error_diagnostic = None
    result = None
    try:
        result = runtime.run()
    except BaseException as error:
        runtime_error = type(error).__name__
        error_diagnostic = _error_diagnostic(error)
    if result is not None and result.stop_reason == "text" and state.status == "running":
        state.status = "done"
    runtime._refresh_usage()
    usage = runtime.token_accounting
    if request["run_kind"] == "fixture":
        token_source = "fixture"
    elif usage in {"provider", "estimated", "mixed"}:
        token_source = usage
    else:
        token_source = "unavailable"
    return {
        "agent_started": True,
        "stop_reason": result.stop_reason if result is not None else "agent_error",
        "state_status": state.status,
        "error_kind": runtime_error,
        "error_diagnostic": error_diagnostic,
        "llm_calls": runtime.llm_calls,
        "successful_responses": response_count,
        "tool_calls": runtime.tool_calls,
        "tool_outcomes": dict(observations.outcomes),
        "permission_denials": observations.permission_denials,
        "input_tokens": runtime.input_tokens,
        "output_tokens": runtime.output_tokens,
        "token_source": token_source,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "invalid_repeat_count": observations.invalid_repeats,
        "subagent_calls": 0,
        "model_binding_ref": binding.reference.to_dict() if binding else None,
        "memory_evidence": {
            "material_sha256": request["memory_material_sha256"],
            "retrieval_occurred": events["retrieval_attempts"] > 0,
            "retrieval_attempts": events["retrieval_attempts"],
            "failure_categories": sorted(set(events["retrieval_failures"])),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    request_path = Path(args[0])
    try:
        if request_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("comparison_request_too_large")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = run_request(request)
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_WORKER_RESULT_BYTES:
            raise ValueError("comparison_worker_result_too_large")
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
        return 0
    except BaseException as error:
        safe = {
            "agent_started": False, "stop_reason": "agent_error",
            "state_status": None, "error_kind": type(error).__name__,
            "llm_calls": 0, "successful_responses": 0, "tool_calls": 0,
            "tool_outcomes": {"succeeded": 0, "failed": 0, "denied": 0, "invalid": 0, "timeout": 0},
            "permission_denials": 0, "input_tokens": None, "output_tokens": None,
            "token_source": "unavailable", "duration_ms": 0, "invalid_repeat_count": 0,
            "subagent_calls": 0, "model_binding_ref": None,
            "memory_evidence": {"material_sha256": None, "retrieval_occurred": False,
                                "retrieval_attempts": 0, "failure_categories": []},
        }
        sys.stdout.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
