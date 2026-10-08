"""Deterministic descriptive metrics for comparison trials."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from statistics import median
from typing import Any


# The frozen experiment contract stays readable; derived report semantics have
# their own version so historical plans need not be changed to correct scoring.
METRICS_RULES_VERSION = "1"
REPORT_RULES_VERSION = "2"

PROVIDER_FAILURES = frozenset({
    "ProviderConnectionError", "ProviderTimeoutError", "ProviderHTTPError",
    "ProviderProtocolError", "ProviderStreamError", "ProviderToolCallShapeError",
    "ProviderToolArgumentsError",
})
WORKER_FAILURES = frozenset({
    "worker_protocol_error", "worker_not_started", "cleanup_incomplete",
    "memory_material_mismatch", "interrupted",
})


def trial_eligibility(trial: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Separate observed grading from qualification for capability scoring."""
    if trial is None:
        return False, "missing_trial"
    agent, grader = trial["agent"], trial["grader"]
    if not trial["cleanup"]["complete"]:
        return False, "cleanup_incomplete"
    if agent["error_kind"] in PROVIDER_FAILURES:
        return False, "provider_failure"
    if agent["error_kind"] in WORKER_FAILURES or agent["stop_reason"] in {"worker_error", "interrupted"}:
        return False, "worker_failure"
    if not agent["started"]:
        return False, "agent_not_started"
    if grader["error_kind"] is not None or type(grader["passed"]) is not bool:
        return False, "grader_unavailable"
    return True, None


def is_scorable(trial: dict[str, Any] | None) -> bool:
    return trial_eligibility(trial)[0]


def percentile(values: list[int], fraction: float) -> float | None:
    """Nearest-rank percentile, matching the existing coding suite report."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return float(ordered[index])


def _total(rows: list[dict[str, Any]], key: str) -> dict[str, int | None]:
    observed = [row[key] for row in rows if type(row.get(key)) is int and row[key] >= 0]
    return {"observed_trials": len(observed), "total": sum(observed) if observed else None}


def _observed_values(values: list[Any]) -> dict[str, int | None]:
    observed = [value for value in values if type(value) is int and value >= 0]
    return {"observed_trials": len(observed), "total": sum(observed) if observed else None}


def _cost_for(trial: dict[str, Any], price: dict[str, Any]) -> str | None:
    agent = trial["agent"]
    input_rate = price.get("input_usd_per_million")
    output_rate = price.get("output_usd_per_million")
    if input_rate is None or output_rate is None:
        return None
    if type(agent.get("input_tokens")) is not int or type(agent.get("output_tokens")) is not int:
        return None
    try:
        value = (
            Decimal(input_rate) * Decimal(agent["input_tokens"])
            + Decimal(output_rate) * Decimal(agent["output_tokens"])
        ) / Decimal(1_000_000)
        return format(value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN), "f")
    except (InvalidOperation, ArithmeticError):
        return None


def paired_cost_delta(left: list[dict[str, Any]], right: list[dict[str, Any]], price: dict[str, Any]) -> str | None:
    if not left or len(left) != len(right):
        return None
    left_costs = [_cost_for(trial, price) for trial in left]
    right_costs = [_cost_for(trial, price) for trial in right]
    if any(value is None for value in left_costs + right_costs):
        return None
    return format(sum((Decimal(value) for value in right_costs), Decimal(0))
                  - sum((Decimal(value) for value in left_costs), Decimal(0)), "f")


def group_metrics(slots: list[dict[str, Any]], trials: list[dict[str, Any]], price: dict[str, Any]) -> dict[str, Any]:
    by_slot = {trial["slot_id"]: trial for trial in trials}
    scorable = [trial for trial in trials if is_scorable(trial)]
    raw_graded = [trial for trial in trials if type(trial["grader"]["passed"]) is bool
                  and trial["grader"]["error_kind"] is None]
    exclusions: dict[str, int] = {}
    for trial in trials:
        eligible, reason = trial_eligibility(trial)
        if not eligible:
            exclusions[reason] = exclusions.get(reason, 0) + 1
    strict = [
        trial for trial in scorable
        if trial["grader"]["passed"] is True
        and (trial["agent"]["successful_responses"] or 0) > 0
        and trial["agent"]["stop_reason"] == "text"
        and trial["agent"]["state_status"] == "done"
        and trial["cleanup"]["complete"] is True
        and trial["agent"]["error_kind"] is None
    ]
    agent_times = [trial["agent"]["duration_ms"] for trial in trials if type(trial["agent"]["duration_ms"]) is int]
    grader_times = [trial["grader"]["duration_ms"] for trial in trials if type(trial["grader"]["duration_ms"]) is int]
    tool_outcomes = {key: 0 for key in ("succeeded", "failed", "denied", "invalid", "timeout")}
    for trial in trials:
        for key, value in trial["agent"]["tool_outcomes"].items():
            tool_outcomes[key] += value
    usage_sources: dict[str, int] = {}
    for trial in trials:
        source = trial["agent"]["token_source"]
        usage_sources[source] = usage_sources.get(source, 0) + 1
    cost_values = [_cost_for(trial, price) for trial in trials]
    total_cost = None
    if trials and all(value is not None for value in cost_values):
        total_cost = format(sum((Decimal(value) for value in cost_values), Decimal(0)), "f")
    per_case: dict[str, Any] = {}
    case_ids = list(dict.fromkeys(slot["case_id"] for slot in slots))
    for case_id in case_ids:
        case_slots = [slot for slot in slots if slot["case_id"] == case_id]
        case_trials = [by_slot[slot["slot_id"]] for slot in case_slots if slot["slot_id"] in by_slot]
        case_scorable = [trial for trial in case_trials if trial in scorable]
        passed = sum(trial["grader"]["passed"] is True for trial in case_scorable)
        durations = [trial["agent"]["duration_ms"] for trial in case_trials if type(trial["agent"]["duration_ms"]) is int]
        grader_durations = [trial["grader"]["duration_ms"] for trial in case_trials if type(trial["grader"]["duration_ms"]) is int]
        per_case[case_id] = {
            "planned": len(case_slots),
            "terminal_slots": sum(slot["status"] in {"completed", "infrastructure_error", "interrupted"} for slot in case_slots),
            "scorable": len(case_scorable),
            "grader_passed": passed,
            "grader_pass_rate": (passed / len(case_scorable)) if case_scorable else None,
            "strict_successes": sum(trial in strict for trial in case_scorable),
            "input_tokens": _observed_values([t["agent"]["input_tokens"] for t in case_trials]),
            "output_tokens": _observed_values([t["agent"]["output_tokens"] for t in case_trials]),
            "agent_duration_ms": {
                "observed_trials": len(durations), "median": median(durations) if durations else None,
                "p95": percentile(durations, .95),
            },
            "grader_duration_ms": {
                "observed_trials": len(grader_durations), "median": median(grader_durations) if grader_durations else None,
                "p95": percentile(grader_durations, .95),
            },
        }
    status_counts: dict[str, int] = {}
    for slot in slots:
        status_counts[slot["status"]] = status_counts.get(slot["status"], 0) + 1
    llm_values = [trial["agent"]["llm_calls"] for trial in trials]
    successful_values = [trial["agent"]["successful_responses"] for trial in trials]
    tool_values = [trial["agent"]["tool_calls"] for trial in trials]
    deny_values = [trial["agent"]["permission_denials"] for trial in trials]
    repeat_values = [trial["agent"]["invalid_repeat_count"] for trial in trials]
    return {
        "planned_slots": len(slots),
        "slot_status_counts": status_counts,
        "run_trials": sum(slot["status"] == "completed" for slot in slots),
        "scorable_trials": len(scorable),
        "independent_acceptance": {"passed": sum(t["grader"]["passed"] is True for t in scorable), "denominator": len(scorable), "rate": sum(t["grader"]["passed"] is True for t in scorable) / len(scorable) if scorable else None},
        "strict_task_success": {"passed": len(strict), "denominator": len(scorable),
                                "rate": len(strict) / len(scorable) if scorable else None},
        "raw_grader": {"passed": sum(t["grader"]["passed"] is True for t in raw_graded),
                       "denominator": len(raw_graded)},
        "exclusion_reasons": exclusions,
        "agent_terminal_states": {
            status: sum(trial["agent"]["state_status"] == status for trial in trials)
            for status in ("done", "blocked", "failed", "running", "awaiting_process", "awaiting_subagents")
        },
        "agent_stop_reasons": {
            reason: sum(trial["agent"]["stop_reason"] == reason for trial in trials)
            for reason in sorted({trial["agent"]["stop_reason"] for trial in trials if trial["agent"]["stop_reason"] is not None})
        },
        "infrastructure_errors": len({slot["slot_id"] for slot in slots
            if slot["status"] == "infrastructure_error"
            or (slot["slot_id"] in by_slot and not is_scorable(by_slot[slot["slot_id"]]))}),
        "unrun_slots": status_counts.get("not_run", 0),
        "agent_llm_calls": _observed_values(llm_values),
        "successful_model_responses": _observed_values(successful_values),
        "tool_calls": _observed_values(tool_values),
        "tool_outcomes": tool_outcomes,
        "permission_denials": _observed_values(deny_values),
        "invalid_repeats": _observed_values(repeat_values),
        "subagent_calls": 0,
        "input_tokens": _observed_values([t["agent"]["input_tokens"] for t in trials]),
        "output_tokens": _observed_values([t["agent"]["output_tokens"] for t in trials]),
        "token_sources": usage_sources,
        "cost_usd": total_cost,
        "cost_observed_trials": sum(value is not None for value in cost_values),
        "cost_note": "原始失败尝试的 token 可能包含保守预算估算，不代表已发生用量；按冻结价格快照和 Decimal 计算；缺价或缺用量时总成本为 null，不表示精确账单。",
        "agent_duration_ms": {"observed_trials": len(agent_times), "median": median(agent_times) if agent_times else None, "p95": percentile(agent_times, .95)},
        "grader_duration_ms": {"observed_trials": len(grader_times), "median": median(grader_times) if grader_times else None, "p95": percentile(grader_times, .95)},
        "cases": per_case,
    }


def delta(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    return right - left


__all__ = ["METRICS_RULES_VERSION", "REPORT_RULES_VERSION", "percentile", "group_metrics",
           "delta", "trial_eligibility", "is_scorable", "paired_cost_delta"]
