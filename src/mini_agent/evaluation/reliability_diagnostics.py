"""Conservative request reservations and bounded numeric diagnostics."""

from __future__ import annotations

from functools import wraps
from fractions import Fraction
from mini_agent.context import count_tokens


from mini_agent.budget import REQUEST_POLICY, TaskBudgetController, request_key

REQUEST_BUDGET_POLICY = dict(REQUEST_POLICY)

LEGACY_REQUEST_BUDGET_POLICY = {
    "initial_input_multiplier": 2,
    "observed_ratio_margin_percent": 10,
    "input_allowance_tokens": 256,
    "minimum_output_tokens": 256,
    "calibration_scope": "trial_including_pre_crash",
}

class RequestDiagnostics:
    def __init__(self, binding, budget: int, *, prior_tokens: int = 0, prior_requests=()):
        self.binding = binding
        self.budget = budget
        self.prior_tokens = prior_tokens
        self.requests: list[dict] = []
        self.current = None
        self.input_multiplier = Fraction(2)
        # Standalone numeric replay also delegates proposals to the common
        # controller. Live observes runtime.budget_proposal directly.
        self._replay_controller = TaskBudgetController(budget)
        self._prior_requests = list(prior_requests[:64])
        self._observed_key = None
        self._live_controller = None

    def before_request(self, runtime, reserved_input: int) -> bool:
        groups = {key: 0 for key in ("system", "state", "notice", "summary", "history")}
        reasoning = 0
        for message in runtime.prepared_messages:
            content = message.get("content", "")
            key = "history"
            if message.get("role") == "system":
                key = "system"
                if isinstance(content, str):
                    for prefix, category in (("[Structured State]", "state"),
                                             ("[Runtime Notice]", "notice"),
                                             ("[Historical Summary]", "summary")):
                        if content.startswith(prefix):
                            key = category
                            break
            groups[key] += count_tokens(message)
            reasoning += count_tokens(message.get("reasoning_content"))
        schemas = [] if getattr(runtime, "finalization", False) else runtime.executor.registry.schemas()
        controller = getattr(runtime, "task_budget", None)
        self._live_controller = controller
        if controller is not None:
            proposal = runtime.budget_proposal
        else:
            controller = self._replay_controller
            controller.data["input_tokens"] = self.prior_tokens + runtime.input_tokens
            controller.data["output_tokens"] = runtime.output_tokens
            key = request_key(getattr(self.binding, "reference", None), schemas, "work")
            if key not in controller.data["samples"]:
                controller.data["samples"][key] = [
                    [row["reserved_input_tokens_estimated"], row["input_tokens"]]
                    for row in self._prior_requests if row.get("usage_source") == "provider"
                    and type(row.get("reserved_input_tokens_estimated")) is int
                    and type(row.get("input_tokens")) is int][-8:]
            self._observed_key = key
            maximum_output = getattr(getattr(self.binding, "profile", None), "max_output_tokens", 8192)
            proposal = controller.proposal(reserved_input, key, maximum_output=maximum_output)
        used, remaining = controller.used, controller.remaining
        reserved_input, calibrated = proposal["raw_input"], proposal["input"]
        output_limit = proposal["output"]
        reason = (None if proposal["admitted"] else
                  "budget_already_exceeded" if used > self.budget else
                  "budget_exhausted" if used == self.budget else
                  proposal.get("reason", "insufficient_request_budget"))
        self.input_multiplier = Fraction(calibrated - 256, reserved_input) if reserved_input else Fraction(2)
        self.current = {
            "request_index": len(self.requests) + 1, "status": "pending",
            "message_tokens_estimated": sum(groups.values()), "message_groups": groups,
            "reasoning_tokens_subset_estimated": reasoning,
            "tool_schema_tokens_estimated": count_tokens(schemas),
            "tool_schema_tokens_by_tool": {
                schema["function"]["name"]: count_tokens(schema) for schema in schemas[:64]
            },
            "reserved_input_tokens_estimated": reserved_input,
            "reserved_input_tokens_calibrated": calibrated,
            "input_multiplier": float(self.input_multiplier),
            "planned_output_limit": output_limit if reason is None else 0,
            "budget_refusal_reason": reason,
            "cumulative_tokens_before": used,
            "remaining_tokens_before": max(0, self.budget - used),
            "max_output_tokens": None, "refusal_reason": None,
            "input_tokens": None, "output_tokens": None, "usage_source": "unobserved",
        }
        if self._live_controller is not None:
            self.current.update(request_kind=proposal["kind"],
                                calibration_key=proposal["key"],
                                finalization=runtime.finalization)
            for field in ("protected_floor_tokens_estimated", "untrimmed_view_tokens_estimated",
                          "input_allowance_tokens_estimated", "closing_reservation_tokens",
                          "completion_blockers", "budget_stage", "input_estimator"):
                self.current[field] = proposal[field]
        if len(self.requests) < 64:
            self.requests.append(self.current)
        return reason is None

    def request_output_limit(self) -> int:
        if self.current is None or not self.current["planned_output_limit"]:
            raise RuntimeError("request has no admitted output reservation")
        return self.current["planned_output_limit"]

    def refuse(self, reason: str) -> None:
        if self.current is not None:
            self.current.update(status="refused", refusal_reason=reason)

    def output_limit(self, value: int) -> None:
        if self.current is not None:
            self.current["max_output_tokens"] = value

    def wrap_client(self, client):
        @wraps(client)
        def observed(messages, **options):
            meter = getattr(self.binding, "usage_meter", None)
            before = meter.snapshot() if meter is not None else None
            row = self.current
            if row is not None:
                row["max_output_tokens"] = options.get("max_output_tokens")
            try:
                result = client(messages, **options)
            except Exception:
                if row is not None:
                    row["status"] = "provider_error"
                    if before is not None:
                        self._usage(row, meter.delta(before))
                raise
            if row is not None:
                row["status"] = "responded"
                if before is not None:
                    self._usage(row, meter.delta(before))
                else:
                    message = getattr(result, "message", result)
                    row.update(input_tokens=count_tokens(messages), output_tokens=count_tokens(message),
                               usage_source="estimated")
            return result
        return observed

    def _usage(self, row, delta):
        if delta["llm_calls"]:
            row.update(input_tokens=delta["input_tokens"], output_tokens=delta["output_tokens"],
                       usage_source=delta["token_accounting"])
            estimate = row["reserved_input_tokens_estimated"]
            if estimate and delta["token_accounting"] == "provider":
                row["observed_input_to_estimate_ratio"] = round(delta["input_tokens"] / estimate, 4)
                if self._live_controller is None and self._observed_key is not None:
                    samples = self._replay_controller.data["samples"][self._observed_key]
                    samples.append([estimate, delta["input_tokens"]])
                    del samples[:-8]

    def snapshot(self, runtime) -> dict:
        controller = getattr(runtime, "task_budget", None)
        used = controller.used if controller else self.prior_tokens + runtime.input_tokens + runtime.output_tokens
        return {"token_budget": self.budget, "prior_phase_tokens": self.prior_tokens,
                "cumulative_tokens_after": used, "budget_overrun_tokens": max(0, used - self.budget),
                "request_budget_policy": dict(REQUEST_BUDGET_POLICY),
                "input_multiplier_after": float(self.input_multiplier),
                "estimation": ("OpenAI Chat complete UTF-8 request size / 3 + 10%"
                               if self._live_controller is not None and self.requests
                               and self.requests[-1].get("input_estimator") == "openai_chat_utf8_request_v1"
                               else "count_tokens; tool schemas reserved separately")
                              + "; not an exact provider tokenizer",
                "requests": self.requests,
                **({"task_budget_ledger": controller.snapshot()} if controller else {})}
