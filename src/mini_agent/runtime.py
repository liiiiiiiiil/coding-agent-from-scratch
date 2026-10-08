"""The canonical parent/child Agent Runtime loop.

The runtime owns the protocol boundary between a model response and the next
model request. Parent and subagent behaviour is supplied by small policies;
policies never call a model or enter a tool handler.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import inspect
import json
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from mini_agent.budget import (TaskBudgetController, BudgetUnavailable, BudgetPersistenceError,
                               request_key, request_input_estimate)
from mini_agent.context import ContextManager, count_tokens
from mini_agent.providers.base import ProviderResponse, UsageMeter
from mini_agent.tools.base import (
    ExecutionResult,
    ToolAdmission,
    ToolExecutor,
    format_tool_result,
)


@dataclass(frozen=True)
class RuntimeResult:
    content: str
    stop_reason: str
    rounds: int
    llm_calls: int
    tool_calls: int
    estimated_tokens: int
    input_tokens: int = 0
    output_tokens: int = 0
    token_accounting: str = "estimated"
    model_binding_ref: Any = None


@dataclass(frozen=True)
class RuntimeDecision:
    action: str
    content: str = ""
    stop_reason: str = ""
    notice: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"continue", "finish"}:
            raise ValueError("RuntimeDecision.action 必须是 continue 或 finish")


@dataclass(frozen=True)
class ToolRoundPlan:
    serial: bool
    rejection_by_index: Mapping[int, ExecutionResult] = field(default_factory=dict)
    parallel_delegation: bool = False
    background_spawn: bool = False


@dataclass(frozen=True)
class NormalizedToolRound:
    calls: tuple[dict[str, Any], ...]
    errors_by_call_id: Mapping[str, str] = field(default_factory=dict)


class LLMMessage(dict):
    """Dict-compatible normalized message with private provider metadata."""

    def __init__(self, message: Mapping[str, Any], response: ProviderResponse | None = None):
        super().__init__(message)
        self.provider_response = response


class RuntimePolicy(Protocol):
    def before_run(self, runtime: "AgentRuntime") -> RuntimeDecision | None: ...

    def before_prepare(self, runtime: "AgentRuntime") -> RuntimeDecision | None: ...

    def before_llm(self, runtime: "AgentRuntime") -> RuntimeDecision | None: ...

    def llm_options(self, runtime: "AgentRuntime") -> dict[str, Any]: ...

    def on_text(self, runtime: "AgentRuntime", content: str) -> RuntimeDecision: ...

    def prepare_tool_round(
        self, runtime: "AgentRuntime", calls: tuple[dict[str, Any], ...]
    ) -> ToolRoundPlan: ...

    def after_tool_result(
        self, runtime: "AgentRuntime", call: dict[str, Any], execution: ExecutionResult
    ) -> str: ...

    def after_tool_round(
        self,
        runtime: "AgentRuntime",
        calls: tuple[dict[str, Any], ...],
        results: tuple[ExecutionResult, ...],
    ) -> RuntimeDecision | None: ...

    def on_round_limit(self, runtime: "AgentRuntime") -> RuntimeDecision: ...


def invoke_llm_once(
    llm_client: Callable,
    messages: list[dict[str, Any]],
    registry: Any = None,
    timeout: float | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Invoke a provider once after adapting its supported keyword surface."""
    kwargs: dict[str, Any] = {
        "include_tools": True,
        "stream_output": False,
        "tool_registry": registry,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    kwargs.update(options)
    try:
        signature = inspect.signature(llm_client)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = signature.parameters
        if not any(item.kind == inspect.Parameter.VAR_KEYWORD
                   for item in parameters.values()):
            kwargs = {name: value for name, value in kwargs.items()
                      if name in parameters}
    response = llm_client(messages, **kwargs)
    if isinstance(response, ProviderResponse):
        return LLMMessage(response.message, response)
    message = response
    if not isinstance(message, dict):
        raise TypeError("LLM 必须返回 message dict")
    if "choices" in message and isinstance(message.get("choices"), list):
        choice = message["choices"][0] if message["choices"] else {}
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            message = choice["message"]
    return LLMMessage(message)


def _invalid_call(
    detail: str,
    *,
    name: str = "invalid_tool_call",
    arguments: dict[str, Any] | None = None,
) -> ExecutionResult:
    text = f"工具调用失败: {detail}"
    return ExecutionResult(
        name,
        arguments or {},
        "not_checked",
        False,
        "invalid",
        0,
        "none",
        text,
        text[:200],
        error_kind="malformed_tool_call",
    )


def normalize_tool_calls(message: dict[str, Any]) -> NormalizedToolRound:
    """Normalize every advertised call and retain one error per bad call."""
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raw_calls = [raw_calls]
    original_ids = {
        call.get("id") for call in raw_calls
        if isinstance(call, dict)
        and isinstance(call.get("id"), str)
        and call.get("id", "").strip()
    }
    normalized: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    seen_ids: set[str] = set()
    next_local_id = 0

    for call in raw_calls:
        call_errors: list[str] = []
        function = call.get("function") if isinstance(call, dict) else None
        raw_id = call.get("id") if isinstance(call, dict) else None
        raw_type = call.get("type") if isinstance(call, dict) else None
        if not isinstance(call, dict):
            call_errors.append("tool_call 格式非法")
        elif raw_type != "function":
            call_errors.append("非法的 tool_call.type")
        if not isinstance(function, dict):
            call_errors.append("tool_call 缺少 function")
            function = {}

        raw_name = function.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            call_errors.append("非法的 function.name")
            name = "invalid_tool_call"
        else:
            name = raw_name

        raw_arguments = function.get("arguments", "{}")
        arguments_text = raw_arguments if isinstance(raw_arguments, str) else "{}"
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
                call_errors.append("非法的 function.arguments")
        else:
            arguments = raw_arguments
            call_errors.append("非法的 function.arguments")
        if not isinstance(arguments, dict):
            arguments = {}
            if "非法的 function.arguments" not in call_errors:
                call_errors.append("非法的 function.arguments")
        if "非法的 function.arguments" in call_errors:
            arguments_text = "{}"

        if not isinstance(raw_id, str) or not raw_id.strip():
            call_errors.append("无效的 tool_call_id")
        elif raw_id in seen_ids:
            call_errors.append("重复的 tool_call_id")

        if call_errors:
            for _ in range(len(raw_calls) + next_local_id + 2):
                call_id = f"local-error-{next_local_id}"
                next_local_id += 1
                if call_id not in seen_ids and call_id not in original_ids:
                    break
            else:
                raise RuntimeError("无法生成唯一的本地 tool_call_id")
            errors[call_id] = "; ".join(call_errors)
        else:
            call_id = raw_id
        seen_ids.add(call_id)
        normalized.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments_text,
            },
        })
    return NormalizedToolRound(tuple(normalized), errors)


class AgentRuntime:
    """Own the single model → tool → observation loop for one agent."""

    def __init__(
        self,
        *,
        llm_client: Callable,
        context: ContextManager,
        executor: ToolExecutor,
        policy: RuntimePolicy,
        max_rounds: int,
        output: Any = None,
        session_boundary: Any = None,
        model_binding: Any = None,
        usage_meter: UsageMeter | None = None,
        token_budget: int | None = None,
    ) -> None:
        if context is None or executor is None:
            raise TypeError("AgentRuntime 需要 context 和 executor")
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds <= 0:
            raise ValueError("max_rounds 必须是正整数")
        self.llm_client = llm_client
        self.context = context
        self.executor = executor
        self.policy = policy
        self.max_rounds = max_rounds
        self.output = output
        self.session_boundary = session_boundary
        delegation_manager = getattr(getattr(executor, "registry", None), "_delegation_manager", None)
        session_store = getattr(session_boundary, "store", None)
        if delegation_manager is not None and session_store is not None:
            delegation_manager.bind_session_root(session_store.root)
        self.model_binding = model_binding or getattr(context, "model_binding", None)
        self.usage_meter = usage_meter or getattr(self.model_binding, "usage_meter", None)
        self._usage_start = self.usage_meter.snapshot() if self.usage_meter is not None else None
        self.rounds = 0
        self.llm_calls = 0
        self.tool_calls = 0
        self.estimated_tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.token_accounting = "estimated"
        self.request_tokens = 0
        self.response_tokens = 0
        self.prepared_messages: list[dict[str, Any]] = []
        self.normalized: NormalizedToolRound | None = None
        self.parsed_calls: list[tuple[str, dict[str, Any]]] = []
        self.effects: list[str] = []
        self.executions: list[ExecutionResult] = []
        self.suppress_next_round_output = False
        self._streamed_content = False
        state = getattr(context, "state", None)
        saved = getattr(state, "task_budget", None)
        enabled = saved is not None or (token_budget is not None and
                                       not getattr(state, "_budget_legacy_resume", False))
        self.task_budget = TaskBudgetController(token_budget, snapshot=saved) if enabled else None
        self._budget_start = self.task_budget.snapshot() if self.task_budget else None
        self.budget_proposal = None
        self.finalization = False
        if self.task_budget is not None:
            state.task_budget = self.task_budget.snapshot()
            # Binding-owned summaries share the very same ledger and meter.
            context.summarizer = self._budgeted_summary if self.model_binding is not None else None

    def _persist_budget(self):
        state = self.context.state
        state.task_budget = self.task_budget.snapshot()
        if self.session_boundary is not None:
            try:
                self.session_boundary.persist_request_budget(state, self.context)
            except Exception as error:
                raise BudgetPersistenceError("task budget commit failed") from error

    def _settle_budget(self, request_id, before):
        delta = self.usage_meter.delta(before) if before is not None else {}
        self.task_budget.settle(request_id, delta.get("input_tokens"), delta.get("output_tokens"),
                                provider=bool(delta.get("llm_calls") and
                                              delta.get("token_accounting") == "provider"))
        self._persist_budget()

    def _budgeted_summary(self, messages, **options):
        key = self._budget_key([], "summary")
        raw = self._request_input_estimate(messages, [], 512)
        proposal = self.task_budget.proposal(raw, key, "summary", self._maximum_output(), self._closing_reservation())
        # A paid summary must cost less than sending the uncompressed view.
        # The budget path already has a cheaper two-round view, so usually the
        # bounded local view wins. Explicit compact still uses this cost gate.
        local = self.context._task_budget_view(
            self.task_budget.remaining, False, observe=False,
        )
        if (not proposal["admitted"] or proposal["input"] + proposal["output"]
                + count_tokens(local) >= count_tokens(self.context._build_messages())):
            raise BudgetUnavailable("summary_is_not_cheaper_than_local_view")
        request_id = self.task_budget.reserve(proposal)
        self._persist_budget()
        before = self.usage_meter.snapshot() if self.usage_meter else None
        try:
            response = self.model_binding.complete(messages, include_tools=False,
                                                   stream_output=False,
                                                   max_output_tokens=proposal["output"])
        finally:
            self._settle_budget(request_id, before)
        if self.task_budget.data["overrun_tokens"]:
            raise BudgetUnavailable("provider_usage_exceeded_reservation")
        return response.message.get("content", "") or ""

    def _maximum_output(self):
        return getattr(getattr(self.model_binding, "profile", None), "max_output_tokens", 8192)

    def _budget_key(self, schemas, kind):
        reference = getattr(self.model_binding, "reference", None)
        return request_key(reference, schemas, kind, estimator=(
            "openai_chat_utf8_request_v1" if self._budget_estimator_name() ==
            "openai_chat_utf8_request_v1" else None))

    def _budget_estimator_name(self):
        reference = getattr(self.model_binding, "reference", None)
        protocol = (reference.get("protocol", "openai_chat") if isinstance(reference, dict) else
                    getattr(reference, "protocol", "openai_chat"))
        return "openai_chat_utf8_request_v1" if protocol == "openai_chat" else "legacy_count_tokens"

    def _request_input_estimate(self, messages, schemas, output_cap):
        if self._budget_estimator_name() != "openai_chat_utf8_request_v1":
            return count_tokens(messages) + count_tokens(schemas)
        profile = getattr(self.model_binding, "profile", None)
        return request_input_estimate(
            messages, schemas, model_id=getattr(profile, "model_id", ""),
            stream=getattr(profile, "supports_streaming", True),
            max_output_tokens=min(self._maximum_output(), output_cap))

    def _closing_reservation(self):
        state = self.context.state
        if state.finalization_ready():
            return 0
        # Verification and plan progress are work performed by the CURRENT
        # request. Reserving them again as future requests makes their funds
        # inaccessible. Only the later, tool-free final reply is ring-fenced.
        # Its floor uses actual authoritative facts, never accumulated rounds.
        floor = self.context._task_budget_view(
            10**12, True, observe=False, recent_rounds=0,
        )
        key = self._budget_key([], "final")
        return self.task_budget.input_reservation(self._request_input_estimate(floor, [], 512) + 256, key) + min(
            self._maximum_output(), 512)

    def _prepare_budget_request(self):
        state = self.context.state
        self.finalization = state.finalization_ready()
        schemas = [] if self.finalization else self.executor.registry.schemas()
        kind = "final" if self.finalization else "work"
        key = self._budget_key(schemas, kind)
        minimum = min(self._maximum_output(), 256)
        blockers = state.completion_blockers()
        # Reserve the protected goal/instructions and authoritative closing
        # facts, not another copy of the accumulated tool history.
        closing = self._closing_reservation()
        allowance = self.task_budget.input_allowance(key, minimum, closing)
        if self.task_budget.remaining < self.task_budget.data["limit"] // 2 and not self.finalization:
            existing_notice = self.context._runtime_notice or ""
            self.context.set_runtime_notice(
                existing_notice + ("\n" if existing_notice else "") +
                f"任务累计 token 预算剩余 {self.task_budget.remaining}，已低于一半。"
                "复用已确认事实，优先完成当前修复、独立验证和计划步骤；避免重复调查。")
        try:
            self.prepared_messages = self.context.prepare_messages(
                input_allowance=max(0, allowance), finalization=self.finalization,
                input_estimator=lambda messages: self._request_input_estimate(
                    messages, schemas, min(self._maximum_output(), 512 if self.finalization else 1024)))
            proposal = self.task_budget.proposal(self._request_input_estimate(
                self.prepared_messages, schemas, min(self._maximum_output(), 512 if self.finalization else 1024)),
                                                key, kind, self._maximum_output(), closing)
        except BudgetUnavailable as error:
            self.prepared_messages = []
            floor = self.context.task_budget_view_diagnostic["protected_floor_tokens_estimated"]
            proposal = self.task_budget.proposal(floor, key, kind,
                                                self._maximum_output(), closing)
            proposal.update(admitted=False, reason=str(error), output=0)
        proposal.update(self.context.task_budget_view_diagnostic)
        proposal.update(closing_reservation_tokens=closing,
                        completion_blockers=list(blockers),
                        input_estimator=self._budget_estimator_name(),
                        budget_stage="final" if self.finalization else
                        "verification_pending" if "verification" in blockers else
                        "plan_progress" if "plan_steps" in blockers else "work")
        self.budget_proposal = proposal

    def _budget_stop(self, reason="insufficient_request_budget"):
        self.context.state.status = "failed"
        self.context.state.terminal_reason = reason
        return self._decision_result(RuntimeDecision("finish", reason, "token_limit"))

    def invoke(
        self,
        messages: list[dict[str, Any]],
        timeout: float | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        """Invoke this runtime's LLM with its own frozen tool surface."""
        registry = getattr(self.executor, "registry", None)
        return invoke_llm_once(
            self.llm_client, messages, registry, timeout, **options,
        )

    def _decision_result(self, decision: RuntimeDecision) -> RuntimeResult:
        self._refresh_usage()
        return RuntimeResult(
            content=str(decision.content),
            stop_reason=decision.stop_reason or decision.action,
            rounds=self.rounds,
            llm_calls=self.llm_calls,
            tool_calls=self.tool_calls,
            estimated_tokens=self.estimated_tokens,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            token_accounting=self.token_accounting,
            model_binding_ref=getattr(self.model_binding, "reference", None),
        )

    def collect_background_events(self) -> list[dict[str, str]]:
        """Collect child completions and render ID-only notices at a safe boundary."""
        manager = getattr(getattr(self.executor, "registry", None), "_delegation_manager", None)
        if manager is None or not hasattr(manager, "collect_background_events"):
            return []
        notices = manager.collect_background_events()
        if self.output is not None:
            for item in notices:
                message = (
                    "后台子代理已收束："
                    f"child_session_id={item['child_session_id']} "
                    f"round_index={item['round_index']} status={item['status']} "
                    f"result_id={item['result_id']}；"
                    "请通过 get_subagent_result 领取正文。"
                )
                if hasattr(self.output, "subagent_notice"):
                    self.output.subagent_notice(message)
                elif hasattr(self.output, "status_notice"):
                    self.output.status_notice(message)
                if hasattr(self.output, "close"):
                    self.output.close()
        return notices

    def _refresh_usage(self) -> None:
        if self.usage_meter is not None and self._usage_start is not None:
            delta = self.usage_meter.delta(self._usage_start)
            self.llm_calls = int(delta["llm_calls"])
            self.input_tokens = int(delta["input_tokens"])
            self.output_tokens = int(delta["output_tokens"])
            self.token_accounting = str(delta["token_accounting"])
        if self.task_budget is not None:
            ledger = self.task_budget.data
            self.input_tokens = ledger["input_tokens"] - self._budget_start["input_tokens"]
            self.output_tokens = ledger["output_tokens"] - self._budget_start["output_tokens"]
            if ledger["unknown_requests"] > self._budget_start["unknown_requests"]:
                self.token_accounting = "mixed" if self.token_accounting == "provider" else "estimated"
        self.estimated_tokens = self.input_tokens + self.output_tokens

    def _apply_decision(self, decision: RuntimeDecision | None) -> RuntimeResult | None:
        if decision is None:
            return None
        if decision.notice is not None and hasattr(self.context, "set_runtime_notice"):
            self.context.set_runtime_notice(decision.notice)
        if decision.action == "finish":
            if self.output is not None and hasattr(self.output, "close"):
                self.output.close()
            return self._decision_result(decision)
        return None

    def _append_assistant(self, message: dict[str, Any]) -> None:
        if hasattr(self.context, "append_assistant"):
            self.context.append_assistant(message)
        else:
            self.context.history.append(message)

    def _append_tool_result(self, call_id: str, content: str) -> None:
        if hasattr(self.context, "append_tool_result"):
            self.context.append_tool_result(call_id, content)
        else:
            self.context.history.append({
                "role": "tool", "tool_call_id": call_id, "content": content,
            })

    def _parse_calls(self, calls: tuple[dict[str, Any], ...]) -> None:
        self.parsed_calls = []
        self.effects = []
        registry = getattr(self.executor, "registry", None)
        structured = registry is not None and hasattr(self.executor, "execute_result")
        for call in calls:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
                if not isinstance(arguments, dict):
                    raise TypeError("tool arguments 必须是 JSON object")
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            name = function.get("name", "invalid_tool_call")
            self.parsed_calls.append((name, arguments))
            try:
                effect = registry.effect_for(name, arguments) if structured else "none"
            except (TypeError, ValueError):
                effect = "none"
            self.effects.append(effect)

    @staticmethod
    def _safe_call_content(execution: ExecutionResult) -> str:
        try:
            return execution.tool_content()
        except Exception as error:
            return f"工具调用失败: {type(error).__name__}"

    @staticmethod
    def _compat_execution(name: str, arguments: dict[str, Any], value: Any) -> ExecutionResult:
        content = format_tool_result(value)
        return ExecutionResult(
            name, arguments, "not_checked", True, "succeeded", 0, "none",
            value, content[:200],
        )

    @staticmethod
    def _exception_execution(name: str, arguments: dict[str, Any], error: Exception) -> ExecutionResult:
        text = f"工具调用失败: {type(error).__name__}"
        return ExecutionResult(
            name, arguments, "not_checked", False, "failed", 0, "none",
            text, text[:200], error_kind="executor_exception",
        )

    def _execute_call(
        self,
        index: int,
        *,
        admission: ToolAdmission | None = None,
        admit_only: bool = False,
    ) -> tuple[ExecutionResult | None, ExecutionResult]:
        name, arguments = self.parsed_calls[index]
        invocation_id = f"r-{self.rounds}-c-{index}"
        structured = hasattr(self.executor, "execute_result")
        if structured and (self.session_boundary is not None or admission is not None):
            if admission is None:
                admission = self.executor.admit(name, arguments, self.context.state)
                if isinstance(admission, ToolAdmission):
                    self.session_boundary.record_admission(
                        invocation_id, admission, self.context.state, self.context,
                    )
            if isinstance(admission, ToolAdmission):
                if admit_only:
                    return None, admission
                execution = self.executor.execute_admitted(admission, notify=False)
            else:
                execution = admission
        else:
            try:
                if structured:
                    execution = self.executor.execute_result(
                        name,
                        arguments,
                        state=getattr(self.context, "state", None),
                        notify=False,
                    )
                else:
                    return None, self._compat_execution(
                        name, arguments, self.executor.execute(name, arguments),
                    )
            except Exception as error:
                return self._exception_execution(name, arguments, error), self._exception_execution(
                    name, arguments, error,
                )
        if isinstance(execution, ToolAdmission):
            execution = self.executor.execute_admitted(execution, notify=False)
        if not isinstance(execution, ExecutionResult):
            execution = self._exception_execution(
                name, arguments, TypeError("executor 必须返回 ExecutionResult"),
            )
        return execution, execution

    def _commit_one(
        self,
        index: int,
        call: dict[str, Any],
        actual: ExecutionResult | None,
        display: ExecutionResult,
        content: str,
    ) -> None:
        structured = hasattr(self.executor, "execute_result")
        name = self.parsed_calls[index][0]
        attempt = None
        if structured and actual is not None and name != "recover":
            state = getattr(self.context, "state", None)
            if state is not None and hasattr(state, "record_execution_result"):
                attempt = state.record_execution_result(actual)
        state = getattr(self.context, "state", None)
        background_claim = None
        if name == "get_subagent_result" and state is not None:
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict) and parsed.get("result_id") and parsed.get("delegation_id"):
                state.commit_delegation_tool_result(content)
                background_claim = (
                    parsed.get("subagent_id"), parsed.get("result_id"),
                )
        durable_delegation = (
            name == "delegate_task"
            and self.session_boundary is not None
            and hasattr(self.session_boundary, "commit_delegation_result")
            and isinstance(getattr(self.session_boundary, "boundary", None), dict)
            and any(item.get("invocation_id") == f"r-{self.rounds}-c-{index}"
                    for item in self.session_boundary.boundary.get(
                        "pending_delegation_results", []
                    ))
        )
        if durable_delegation:
            # The State lifecycle, parent attempt, role=tool message and
            # pending raw result are exported by one boundary save.  In-memory
            # State is updated first so a failed save stops the loop before a
            # subsequent provider request.
            if state is not None and hasattr(state, "commit_delegation_tool_result"):
                state.commit_delegation_tool_result(content)
            self._append_tool_result(call["id"], content)
            self.session_boundary.commit_delegation_result(
                f"r-{self.rounds}-c-{index}", actual or display, content,
                state, self.context, attempt,
            )
            return
        self._append_tool_result(call["id"], content)
        if self.session_boundary is not None:
            self.session_boundary.record_execution_result(
                f"r-{self.rounds}-c-{index}",
                actual or display,
                content,
                state,
                self.context,
                attempt,
            )
        if name == "delegate_task" and state is not None and hasattr(state, "commit_delegation_tool_result"):
            state.commit_delegation_tool_result(content)
        if background_claim is not None:
            manager = getattr(getattr(self.executor, "registry", None), "_delegation_manager", None)
            if manager is not None:
                manager.mark_background_claimed(*background_claim)

    def _start_durable_round(self, calls: tuple[dict[str, Any], ...]) -> None:
        if self.session_boundary is None:
            return
        history = getattr(self.context, "history", [])
        round_id = sum(
            1 for item in history
            if isinstance(item, dict)
            and item.get("role") == "assistant"
            and item.get("tool_calls")
        )
        self.session_boundary.start_round(
            max(1, round_id),
            history[-1],
            [
                {
                    "invocation_id": f"r-{self.rounds}-c-{index}",
                    "tool_call_id": call["id"],
                    "tool": self.parsed_calls[index][0],
                    "arguments": self.parsed_calls[index][1],
                    "effect_class": self.effects[index],
                }
                for index, call in enumerate(calls)
            ],
            getattr(self.context, "state", None),
            self.context,
        )

    def _run_tool_call(
        self,
        index: int,
        call: dict[str, Any],
        plan: ToolRoundPlan,
    ) -> tuple[ExecutionResult | None, ExecutionResult, str]:
        if self.normalized and call["id"] in self.normalized.errors_by_call_id:
            execution = _invalid_call(
                self.normalized.errors_by_call_id[call["id"]],
                name=self.parsed_calls[index][0],
                arguments=self.parsed_calls[index][1],
            )
            content = self.policy.after_tool_result(self, call, execution)
            return execution, execution, format_tool_result(content)
        if index in plan.rejection_by_index:
            execution = plan.rejection_by_index[index]
            actual = execution if execution.error_kind in {
                "mixed_verification", "repair_phase_gate", "invalid_result",
                "budget_exhausted", "invalid_tool_call",
            } else None
            content = self.policy.after_tool_result(self, call, execution)
            return actual, execution, format_tool_result(content)
        actual, display = self._execute_call(index)
        content = self.policy.after_tool_result(self, call, display)
        if not isinstance(content, str):
            content = format_tool_result(content)
        return actual, display, content

    def _run_tool_round(
        self,
        calls: tuple[dict[str, Any], ...],
        plan: ToolRoundPlan,
    ) -> tuple[ExecutionResult, ...]:
        self._start_durable_round(calls)
        if self.output is not None and hasattr(self.output, "tools_start"):
            self.output.tools_start(calls)
        if plan.background_spawn:
            return self._run_background_spawn_round(calls, plan)
        if plan.parallel_delegation:
            return self._run_parallel_delegation_round(calls, plan)
        records: list[tuple[ExecutionResult | None, ExecutionResult, str] | None] = [None] * len(calls)
        if plan.serial:
            for index, call in enumerate(calls):
                record = self._run_tool_call(index, call, plan)
                records[index] = record
                self._commit_one(index, call, *record)
        elif self.session_boundary is not None and hasattr(self.executor, "admit"):
            staged: dict[int, ToolAdmission] = {}
            immediate: dict[int, tuple[ExecutionResult | None, ExecutionResult, str]] = {}
            for index, call in enumerate(calls):
                if self.normalized and call["id"] in self.normalized.errors_by_call_id:
                    immediate[index] = self._run_tool_call(index, call, plan)
                    continue
                if index in plan.rejection_by_index:
                    immediate[index] = self._run_tool_call(index, call, plan)
                    continue
                name, arguments = self.parsed_calls[index]
                admission = self.executor.admit(name, arguments, self.context.state)
                if isinstance(admission, ToolAdmission):
                    self.session_boundary.record_admission(
                        f"r-{self.rounds}-c-{index}",
                        admission,
                        self.context.state,
                        self.context,
                    )
                    staged[index] = admission
                else:
                    immediate[index] = (
                        admission,
                        admission,
                        self.policy.after_tool_result(self, call, admission),
                    )
            with ThreadPoolExecutor(max_workers=max(1, len(staged))) as pool:
                futures = {
                    index: pool.submit(
                        self._execute_call, index, admission=admission,
                    )
                    for index, admission in staged.items()
                }
                for index, call in enumerate(calls):
                    if index in immediate:
                        record = immediate[index]
                    else:
                        actual, display = futures[index].result()
                        record = (
                            actual,
                            display,
                            self.policy.after_tool_result(self, call, display),
                        )
                    records[index] = record
                    self._commit_one(index, call, *record)
        else:
            with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
                futures = {
                    index: pool.submit(self._run_tool_call, index, call, plan)
                    for index, call in enumerate(calls)
                }
                for index, call in enumerate(calls):
                    record = futures[index].result()
                    records[index] = record
                    self._commit_one(index, call, *record)

        if self.output is not None and hasattr(self.output, "tool_result"):
            for call, record in zip(calls, records):
                actual, display, content = record
                function = call.get("function", {})
                rendered_execution = display
                if actual is None and not hasattr(self.executor, "execute_result"):
                    rendered_execution = None
                self.output.tool_result(
                    function.get("name", "<missing>"),
                    function.get("arguments", "{}"),
                    content,
                    rendered_execution,
                )
            if hasattr(self.output, "close"):
                self.output.close()
        if self.session_boundary is not None:
            self.session_boundary.complete_round(
                getattr(self.context, "state", None), self.context,
            )
        self.executions = [record[1] for record in records]
        self.tool_calls += len(calls)
        return tuple(self.executions)

    def _run_background_spawn_round(
        self, calls: tuple[dict[str, Any], ...], plan: ToolRoundPlan,
    ) -> tuple[ExecutionResult, ...]:
        """Commit pure spawn confirmations in model order before starting workers."""
        records: list[tuple[ExecutionResult | None, ExecutionResult, str] | None] = [None] * len(calls)
        manager = getattr(getattr(self.executor, "registry", None), "_delegation_manager", None)
        for index, call in enumerate(calls):
            if self.normalized and call["id"] in self.normalized.errors_by_call_id:
                record = self._run_tool_call(index, call, plan)
            elif index in plan.rejection_by_index:
                record = self._run_tool_call(index, call, plan)
            else:
                name, arguments = self.parsed_calls[index]
                admission = self.executor.admit(name, arguments, self.context.state)
                if isinstance(admission, ToolAdmission):
                    if self.session_boundary is not None:
                        self.session_boundary.record_admission(
                            f"r-{self.rounds}-c-{index}", admission,
                            self.context.state, self.context,
                        )
                    actual, display = self._execute_call(index, admission=admission)
                else:
                    actual, display = admission, admission
                content = self.policy.after_tool_result(self, call, display)
                if not isinstance(content, str):
                    content = format_tool_result(content)
                record = (actual, display, content)
            records[index] = record
            self._commit_one(index, call, *record)
            if (manager is not None and record[1].outcome == "succeeded"
                    and isinstance(record[1].output, str)):
                try:
                    confirmation = json.loads(record[1].output)
                except (TypeError, ValueError, json.JSONDecodeError):
                    confirmation = None
                if (isinstance(confirmation, dict) and confirmation.get("accepted") is True
                        and isinstance(confirmation.get("child_session_id"), str)):
                    manager.confirm_background_startup(confirmation["child_session_id"])

        if self.output is not None and hasattr(self.output, "tool_result"):
            for call, record in zip(calls, records):
                _actual, display, content = record
                function = call.get("function", {})
                self.output.tool_result(
                    function.get("name", "<missing>"),
                    function.get("arguments", "{}"), content, display,
                )
            if hasattr(self.output, "close"):
                self.output.close()
        if self.session_boundary is not None:
            self.session_boundary.complete_round(
                getattr(self.context, "state", None), self.context,
            )
        if manager is not None:
            # Workers are released only after the entire ordered confirmation
            # boundary has been committed.
            manager.commit_background_spawn_round()
            manager.activate_background_tasks()
        self.executions = [record[1] for record in records]
        self.tool_calls += len(calls)
        return tuple(self.executions)

    def _run_parallel_delegation_round(
        self,
        calls: tuple[dict[str, Any], ...],
        plan: ToolRoundPlan,
    ) -> tuple[ExecutionResult, ...]:
        """Run a pure delegate batch through its dedicated scheduler.

        Admission and durable ``handler_admitted`` commits stay in model
        order.  Only the frozen child workers may complete out of order; the
        callback below is invoked by the scheduler for the next ready prefix.
        """
        records: list[tuple[ExecutionResult | None, ExecutionResult, str] | None] = [None] * len(calls)
        admissions: dict[int, ToolAdmission] = {}
        immediate: dict[int, tuple[ExecutionResult | None, ExecutionResult, str]] = {}
        for index, call in enumerate(calls):
            if self.normalized and call["id"] in self.normalized.errors_by_call_id:
                immediate[index] = self._run_tool_call(index, call, plan)
                continue
            if index in plan.rejection_by_index:
                immediate[index] = self._run_tool_call(index, call, plan)
                continue
            name, arguments = self.parsed_calls[index]
            admission = self.executor.admit(name, arguments, self.context.state)
            if isinstance(admission, ToolAdmission):
                if self.session_boundary is not None:
                    self.session_boundary.record_admission(
                        f"r-{self.rounds}-c-{index}", admission,
                        self.context.state, self.context,
                    )
                admissions[index] = admission
            else:
                content = self.policy.after_tool_result(self, call, admission)
                if not isinstance(content, str):
                    content = format_tool_result(content)
                immediate[index] = (admission, admission, content)

        manager = getattr(getattr(self.executor, "registry", None), "_delegation_manager", None)
        if manager is None or not hasattr(manager, "prepare_batch"):
            # This should only be reachable for a custom registry.  Preserve
            # the normal executor behavior rather than bypassing a handler.
            for index, call in enumerate(calls):
                if index not in immediate:
                    admission = admissions[index]
                    execution = self.executor.execute_admitted(admission, notify=False)
                    content = self.policy.after_tool_result(self, call, execution)
                    if not isinstance(content, str):
                        content = format_tool_result(content)
                    immediate[index] = (execution, execution, content)
            for index, call in enumerate(calls):
                record = immediate[index]
                records[index] = record
                self._commit_one(index, call, *record)
        else:
            tasks, ready, rejected_tasks = manager.prepare_batch(admissions, self.context.state)

            if self.session_boundary is not None and tasks and hasattr(
                    self.session_boundary, "persist_delegation_batch"):
                # All accepted contracts, reservations and running lifecycle
                # records are durable before the first child worker starts.
                self.session_boundary.persist_delegation_batch(
                    tasks, self.context.state, self.context,
                )

            def commit_ready(index: int, result: Any) -> None:
                if isinstance(result, tuple) and len(result) == 3:
                    record = result
                elif index in admissions:
                    admission = admissions[index]
                    execution = self.executor.execute_admitted_delegation(admission, result)
                    call = calls[index]
                    content = self.policy.after_tool_result(self, call, execution)
                    if not isinstance(content, str):
                        content = format_tool_result(content)
                    record = (execution, execution, content)
                else:
                    # A contract failure happened after ToolExecutor's normal
                    # admission boundary.  Keep a unique bounded tool result,
                    # but do not claim that its handler ran.
                    name, arguments = self.parsed_calls[index]
                    content = result.to_json() if hasattr(result, "to_json") else format_tool_result(result)
                    execution = ExecutionResult(
                        name, arguments, "not_checked", False, "invalid", 0,
                        self.effects[index], content, content[:200],
                        error_kind=getattr(result, "error_kind", "invalid_contract"),
                    )
                    content = self.policy.after_tool_result(self, calls[index], execution)
                    record = (None, execution, format_tool_result(content))
                records[index] = record
                self._commit_one(index, calls[index], *record)

            # Immediate results are safe to deliver only through the same
            # ordered prefix.  The scheduler includes them in its ready map.
            for index, record in immediate.items():
                records[index] = record
            all_ready = dict(ready)
            all_ready.update(immediate)
            manager.run_prepared_batch(
                tasks, self.context.state, ready=all_ready,
                rejected_tasks=rejected_tasks,
                on_result_ready=(
                    (lambda index, result: self.session_boundary.record_delegation_result_ready(
                        f"r-{self.rounds}-c-{index}", result,
                        self.context.state, self.context,
                    ))
                    if self.session_boundary is not None
                    and hasattr(self.session_boundary, "record_delegation_result_ready")
                    else None
                ),
                on_result=commit_ready,
            )
            if any(record is None for record in records):
                raise RuntimeError("委派调度器未返回对应结果")

        if self.output is not None and hasattr(self.output, "tool_result"):
            for call, record in zip(calls, records):
                actual, display, content = record
                function = call.get("function", {})
                self.output.tool_result(
                    function.get("name", "<missing>"),
                    function.get("arguments", "{}"), content, display,
                )
            if hasattr(self.output, "close"):
                self.output.close()
        if self.session_boundary is not None:
            self.session_boundary.complete_round(
                getattr(self.context, "state", None), self.context,
            )
        self.executions = [record[1] for record in records]
        self.tool_calls += len(calls)
        return tuple(self.executions)

    def run(self) -> RuntimeResult:
        early = self._apply_decision(self.policy.before_run(self))
        if early is not None:
            return early
        while self.rounds < self.max_rounds:
            early = self._apply_decision(self.policy.before_prepare(self))
            if early is not None:
                return early
            if self.task_budget is not None:
                if self.task_budget.reconcile_pending():
                    self._persist_budget()
                self._prepare_budget_request()
            else:
                self.prepared_messages = self.context.prepare_messages()
            self._refresh_usage()
            self.request_tokens = count_tokens(self.prepared_messages)
            early = self._apply_decision(self.policy.before_llm(self))
            if early is not None:
                return early
            if self.task_budget is not None and not self.budget_proposal["admitted"]:
                return self._budget_stop(self.budget_proposal.get("reason", "insufficient_request_budget"))
            options = self.policy.llm_options(self)
            request_id = None
            if self.task_budget is not None:
                request_id = self.task_budget.reserve(self.budget_proposal)
                self._persist_budget()
                options = dict(options, max_output_tokens=self.budget_proposal["output"],
                               include_tools=not self.finalization)
            self.rounds += 1
            self.llm_calls += 1
            if (not self.suppress_next_round_output and self.output is not None
                    and hasattr(self.output, "round_start")):
                self.output.round_start(self.rounds)
            self.suppress_next_round_output = False
            self._streamed_content = False
            # Count the request before invoking the provider so timeout and
            # provider-error results still report consumed input tokens.
            self.input_tokens += self.request_tokens
            self.estimated_tokens = self.input_tokens + self.output_tokens
            meter_before = self.usage_meter.snapshot() if self.usage_meter is not None else None
            try:
                message = self.invoke(
                    self.prepared_messages,
                    **options,
                )
            except Exception:
                if request_id is not None:
                    self._settle_budget(request_id, meter_before)
                if (self.usage_meter is not None and meter_before is not None
                        and self.usage_meter.snapshot()["llm_calls"] == meter_before["llm_calls"]):
                    self.usage_meter.record(
                        None, estimated_input_tokens=self.request_tokens,
                    )
                self._refresh_usage()
                raise
            if request_id is not None:
                self._settle_budget(request_id, meter_before)
            self.response_tokens = count_tokens(message)
            if (self.usage_meter is not None and meter_before is not None
                    and self.usage_meter.snapshot()["llm_calls"] == meter_before["llm_calls"]):
                self.usage_meter.record(
                    None,
                    estimated_input_tokens=self.request_tokens,
                    estimated_output_tokens=self.response_tokens,
                )
            if self.usage_meter is None:
                self.output_tokens += self.response_tokens
            self._refresh_usage()
            if (not self._streamed_content and message.get("content")
                    and self.output is not None and hasattr(self.output, "assistant_delta")):
                self.output.assistant_delta(message["content"])
            if self.output is not None and hasattr(self.output, "assistant_end"):
                self.output.assistant_end()

            after_llm = getattr(self.policy, "after_llm", None)
            budget_overrun = bool(self.task_budget and self.task_budget.data["overrun_tokens"])
            if after_llm is not None and not budget_overrun:
                early = self._apply_decision(after_llm(self, message))
                if early is not None:
                    return early

            normalized = normalize_tool_calls(message)
            self.normalized = normalized
            assistant = dict(message)
            assistant["tool_calls"] = list(normalized.calls)
            if not normalized.calls:
                assistant.pop("tool_calls", None)
            self._append_assistant(assistant)
            if not normalized.calls:
                if budget_overrun:
                    return self._budget_stop("provider_usage_exceeded_reservation")
                decision = self.policy.on_text(
                    self, message.get("content", "") or "",
                )
                if (self.task_budget is not None and decision.action == "finish"
                        and decision.stop_reason == "text" and self.context.state.completion_blockers()):
                    self.context.state.status = "blocked"
                    self.context.state.terminal_reason = "completion_blocked"
                    decision = RuntimeDecision("finish", "completion_blocked", "blocked")
                early = self._apply_decision(decision)
                if early is not None:
                    return early
                continue

            self._parse_calls(normalized.calls)
            plan = self.policy.prepare_tool_round(self, normalized.calls)
            if not isinstance(plan, ToolRoundPlan):
                raise TypeError("RuntimePolicy.prepare_tool_round 必须返回 ToolRoundPlan")
            budget_rejection = budget_overrun or (self.task_budget is not None and self.finalization)
            if budget_rejection:
                reason = "provider_usage_exceeded_reservation" if budget_overrun else "finalization_tools_forbidden"
                rejected = {
                    index: ExecutionResult(name, arguments, "not_checked", False, "denied", 0,
                                           self.effects[index], reason, reason, error_kind="budget_exhausted")
                    for index, (name, arguments) in enumerate(self.parsed_calls)
                }
                plan = ToolRoundPlan(serial=True, rejection_by_index=rejected)
            self._run_tool_round(normalized.calls, plan)
            if budget_rejection:
                if budget_overrun:
                    return self._budget_stop("provider_usage_exceeded_reservation")
                self.context.state.status = "blocked"
                self.context.state.terminal_reason = "finalization_tools_forbidden"
                return self._decision_result(RuntimeDecision("finish", reason, "blocked"))
            decision = self.policy.after_tool_round(
                self, normalized.calls, tuple(self.executions),
            )
            early = self._apply_decision(decision)
            if early is not None:
                return early

        decision = self.policy.on_round_limit(self)
        if decision.action != "finish":
            decision = RuntimeDecision(
                "finish", decision.content, decision.stop_reason or "round_limit",
            )
        return self._apply_decision(decision) or self._decision_result(decision)
