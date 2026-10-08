"""Read-only reconstruction of comparison reports from frozen raw evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from statistics import median
import stat
import tempfile
from typing import Any

from mini_agent.evaluation.benchmark import validate_suite
from mini_agent.evaluation.comparison_metrics import (
    METRICS_RULES_VERSION, REPORT_RULES_VERSION, group_metrics, is_scorable, trial_eligibility, paired_cost_delta,
)
from mini_agent.evaluation.comparison_schema import (
    MAX_COMPARISON_RUN_BYTES, MAX_COMPARISON_SPEC_BYTES, MAX_COMPARISON_TRIAL_BYTES,
    canonical_json, comparison_execution_order, sha256_bytes, sha256_json, validate_comparison_run,
    validate_comparison_spec, validate_comparison_trial,
)


def _read_regular(path: Path, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ValueError(f"evidence is missing or not a regular file: {path.name}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError(f"evidence exceeds its limit: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            data = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > limit:
        raise ValueError(f"evidence exceeds its limit: {path.name}")
    return data


def _safe_path(root: Path, relative: str) -> Path:
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("comparison evidence path contains a symlink")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("comparison evidence path escapes run directory")
    return candidate


def _read_regular_below(root: Path, relative: str, limit: int) -> bytes:
    """Open each evidence path component without following a swapped symlink."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("comparison evidence path is invalid")
    if not hasattr(os, "O_DIRECTORY") or os.open not in getattr(os, "supports_dir_fd", set()):
        return _read_regular(_safe_path(root, relative), limit)
    current_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
    except OSError as error:
        raise ValueError("comparison evidence path is missing or contains a link") from error
    finally:
        if current_fd >= 0:
            os.close(current_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("comparison evidence exceeds its limit")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            data = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > limit:
        raise ValueError("comparison evidence exceeds its limit")
    return data


def _read_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(path, limit).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON evidence: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence must be an object: {path.name}")
    return value


def _verify_plan(raw: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "plan_id", "comparison_id", "spec", "spec_sha256",
        "suite_summary", "source_snapshots", "adapter_sha256", "environment",
        "model_binding_ref", "execution_order", "memory_material_summary",
        "offline_baseline_outcomes", "plan_sha256",
    }
    if set(raw) != expected or raw.get("schema_version") != 1:
        raise ValueError("comparison-plan.json contract is invalid")
    spec = validate_comparison_spec(raw["spec"]).value
    if spec["metrics_rules_version"] != METRICS_RULES_VERSION:
        raise ValueError("comparison plan metrics rule version is unsupported")
    if raw["spec_sha256"] != sha256_json(spec):
        raise ValueError("comparison plan spec hash mismatch")
    body = dict(raw)
    claimed = body.pop("plan_sha256")
    if claimed != sha256_json(body):
        raise ValueError("comparison plan hash mismatch")
    if raw["comparison_id"] != spec["comparison_id"]:
        raise ValueError("comparison plan ID mismatch")
    if raw["suite_summary"].get("suite_id") != spec["suite"]["suite_id"] or raw["suite_summary"].get("version") != spec["suite"]["version"] or raw["suite_summary"].get("sha256") != spec["suite"]["sha256"]:
        raise ValueError("comparison plan suite summary differs from the spec")
    case_rows = raw["suite_summary"].get("cases")
    expected_cases = [item["case_id"] for item in spec["memory_materials"]]
    if not isinstance(case_rows, list) or [item.get("case_id") for item in case_rows if isinstance(item, dict)] != expected_cases:
        raise ValueError("comparison plan suite cases differ from the Memory material order")
    case_fields = {
        "case_id", "case_version", "task", "task_sha256", "initial_sha256", "grader_sha256",
        "known_good_sha256", "allowed_tools", "authorized_tools", "max_rounds",
        "agent_timeout_seconds", "grader_timeout_seconds",
    }
    for item in case_rows:
        if (not isinstance(item, dict) or set(item) != case_fields
                or not isinstance(item["task"], str) or len(item["task"]) > 12_000
                or sha256_bytes(item["task"].encode("utf-8")) != item["task_sha256"]
                or any(not isinstance(item[key], str) or len(item[key]) != 64 for key in ("task_sha256", "initial_sha256", "grader_sha256", "known_good_sha256"))
                or not isinstance(item["allowed_tools"], list)
                or not isinstance(item["authorized_tools"], list)
                or not set(item["authorized_tools"]).issubset(item["allowed_tools"])):
            raise ValueError("comparison plan suite case contract is invalid")
    source_snapshots = raw["source_snapshots"]
    if not isinstance(source_snapshots, dict) or set(source_snapshots) != {item["group_id"] for item in spec["groups"]}:
        raise ValueError("comparison plan source snapshot groups are incomplete")
    for group in spec["groups"]:
        snapshot = source_snapshots[group["group_id"]]
        expected_version = "0.52.0" if group["group_id"] == "old-off" else "0.53.0"
        if (not isinstance(snapshot, dict) or set(snapshot) != {"revision", "package_version", "runtime_fingerprint"}
                or snapshot["revision"] != group["source_revision"] or snapshot["package_version"] != expected_version
                or not isinstance(snapshot["runtime_fingerprint"], str) or len(snapshot["runtime_fingerprint"]) != 64):
            raise ValueError("comparison plan source snapshot is invalid")
    expected_order = comparison_execution_order(expected_cases, spec["groups"], spec["repeats"])
    if raw["execution_order"] != expected_order:
        raise ValueError("comparison plan execution order is invalid")
    materials = [
        {"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]}
        for item in spec["memory_materials"]
    ]
    memory_summary = raw["memory_material_summary"]
    if (not isinstance(memory_summary, dict) or set(memory_summary) != {"semantic_digest", "materials"}
            or memory_summary["materials"] != materials
            or memory_summary["semantic_digest"] != spec["groups"][0]["memory_material_sha256"]):
        raise ValueError("comparison plan Memory material summary is invalid")
    expected_baselines = [
        outcome for case_id in expected_cases
        for outcome in ({"case_id": case_id, "tree": "initial", "passed": False},
                       {"case_id": case_id, "tree": "known_good", "passed": True})
    ]
    if raw["offline_baseline_outcomes"] != expected_baselines:
        raise ValueError("comparison plan offline grader calibration is invalid")
    if not isinstance(raw["adapter_sha256"], str) or len(raw["adapter_sha256"]) != 64:
        raise ValueError("comparison plan adapter fingerprint is invalid")
    environment = raw["environment"]
    environment_fields = {"python_version", "python_implementation", "platform_system", "platform_release", "platform_machine"}
    if not isinstance(environment, dict) or set(environment) != environment_fields or not all(isinstance(value, str) and value for value in environment.values()):
        raise ValueError("comparison plan environment summary is invalid")
    binding = raw["model_binding_ref"]
    if binding is not None and (
        not isinstance(binding, dict) or set(binding) != {"profile", "provider", "protocol", "fingerprint"}
        or binding["profile"] != spec["groups"][0]["model_profile"]
        or binding["protocol"] not in {"openai_chat", "anthropic_messages"}
        or not isinstance(binding["fingerprint"], str) or len(binding["fingerprint"]) != 64
    ):
        raise ValueError("comparison plan model binding summary is invalid")
    return raw


def _valid_factor_edge(spec: dict[str, Any], edge: dict[str, Any]) -> list[str]:
    groups = {item["group_id"]: item for item in spec["groups"]}
    left = groups[edge["baseline_group"]]
    right = groups[edge["experiment_group"]]
    differing = {key for key in left if key != "group_id" and left[key] != right[key]}
    if differing != set(edge["allowed_changes"]):
        return ["declared_comparison_factors_do_not_match_group_conditions"]
    return []


def _strict_success(trial: dict[str, Any] | None) -> bool | None:
    if not is_scorable(trial):
        return None
    return bool(
        (trial["agent"]["successful_responses"] or 0) > 0
        and trial["agent"]["stop_reason"] == "text"
        and trial["agent"]["state_status"] == "done"
        and trial["agent"]["error_kind"] is None
        and trial["grader"]["passed"] is True
        and trial["grader"]["error_kind"] is None
        and trial["cleanup"]["complete"] is True
    )


def _evidence(trial: dict[str, Any] | None) -> dict[str, Any] | None:
    if trial is None:
        return None
    return {
        "slot_id": trial["slot_id"],
        "artifacts": {key: item["path"] for key, item in trial["evidence"]["artifacts"].items()},
    }


def build_comparison_report(run_dir: str | Path) -> dict[str, Any]:
    """Validate all links/digests and rebuild group and paired-edge metrics."""
    root = Path(run_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("comparison run path must be a directory")
    run = validate_comparison_run(_read_json(root / "comparison-run.json", MAX_COMPARISON_RUN_BYTES)).value
    plan = _verify_plan(_read_json(root / "comparison-plan.json", MAX_COMPARISON_RUN_BYTES))
    spec = validate_comparison_spec(plan["spec"]).value
    if run["comparison_id"] != spec["comparison_id"] or run["spec_sha256"] != plan["spec_sha256"] or run["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("comparison run is not bound to its frozen plan")
    if run["suite"] != plan["suite_summary"] or run["frozen_sources"] != plan["source_snapshots"]:
        raise ValueError("comparison run source summaries differ from the plan")
    if run["environment"] != plan["environment"] or run["model_binding_ref"] != plan["model_binding_ref"]:
        raise ValueError("comparison run environment or binding differs from the plan")
    if run["execution_order"] != plan["execution_order"]:
        raise ValueError("comparison run execution order differs from the plan")

    spec_path = root / "comparison-spec.json"
    saved_spec = validate_comparison_spec(_read_json(spec_path, MAX_COMPARISON_SPEC_BYTES)).value
    if saved_spec != spec:
        raise ValueError("run copy of ComparisonSpec differs from the frozen plan")
    try:
        suite_copy = json.loads(_read_regular(root / "suite.json", 1024 * 1024).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid frozen suite evidence") from error
    if not isinstance(suite_copy, dict):
        raise ValueError("frozen suite evidence must be an object")
    suite_manifest = dict(suite_copy)
    claimed_suite_sha = suite_manifest.pop("suite_sha256", None)
    calculated_suite_sha = sha256_bytes(canonical_json(suite_manifest))
    if claimed_suite_sha != spec["suite"]["sha256"] or calculated_suite_sha != claimed_suite_sha:
        raise ValueError("frozen suite evidence digest mismatch")
    validated_manifest = validate_suite(suite_copy)
    manifest_cases = validated_manifest["cases"]
    summary_cases = plan["suite_summary"]["cases"]
    if [item["case_id"] for item in manifest_cases] != [item["case_id"] for item in summary_cases]:
        raise ValueError("frozen suite manifest case order differs from the plan")
    for manifest_case, summary_case in zip(manifest_cases, summary_cases):
        for field in ("task_sha256", "initial_sha256", "grader_sha256", "known_good_sha256"):
            if manifest_case[field] != summary_case[field]:
                raise ValueError("frozen suite manifest differs from the planned case summary")
    trials_by_slot: dict[str, dict[str, Any]] = {}
    for slot in run["slots"]:
        if slot["trial_path"] is None:
            continue
        _safe_path(root, slot["trial_path"])
        trial_bytes = _read_regular_below(root, slot["trial_path"], MAX_COMPARISON_TRIAL_BYTES)
        if sha256_bytes(trial_bytes) != slot["trial_sha256"]:
            raise ValueError(f"trial digest mismatch for slot {slot['slot_id']}")
        try:
            trial_raw = json.loads(trial_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid trial JSON for slot {slot['slot_id']}") from error
        trial = validate_comparison_trial(trial_raw).value
        if trial["run_id"] != run["run_id"]:
            raise ValueError(f"trial identity mismatch for {slot['slot_id']}: run_id")
        for field in ("slot_id", "group_id", "case_id", "repetition"):
            if trial[field] != slot[field]:
                raise ValueError(f"trial identity mismatch for {slot['slot_id']}: {field}")
        group = next(group for group in spec["groups"] if group["group_id"] == slot["group_id"])
        source = plan["source_snapshots"][group["group_id"]]
        if (trial["source_revision"] != group["source_revision"]
                or trial["source_fingerprint"] != source["runtime_fingerprint"]
                or trial["suite_sha256"] != spec["suite"]["sha256"]
                or trial["memory_retrieval_enabled"] != group["memory_retrieval_enabled"]
                or trial["memory_material_sha256"] != group["memory_material_sha256"]):
            raise ValueError(f"trial frozen conditions mismatch for {slot['slot_id']}")
        for artifact in trial["evidence"]["artifacts"].values():
            _safe_path(root, artifact["path"])
            raw = _read_regular_below(root, artifact["path"], 1024 * 1024)
            if len(raw) != artifact["size_bytes"] or sha256_bytes(raw) != artifact["sha256"]:
                raise ValueError(f"raw artifact digest mismatch for {slot['slot_id']}")
        trials_by_slot[slot["slot_id"]] = trial

    groups: dict[str, Any] = {}
    for group in spec["groups"]:
        selected_slots = [slot for slot in run["slots"] if slot["group_id"] == group["group_id"]]
        selected_trials = [trials_by_slot[slot["slot_id"]] for slot in selected_slots if slot["slot_id"] in trials_by_slot]
        groups[group["group_id"]] = {
            "source_revision": group["source_revision"],
            "memory_retrieval_enabled": group["memory_retrieval_enabled"],
            "model_profile": group["model_profile"],
            **group_metrics(selected_slots, selected_trials, spec["price_snapshot"]),
        }

    all_bindings = [trial["model_binding_ref"] for trial in trials_by_slot.values() if trial["model_binding_ref"] is not None]
    binding_mismatch = bool(run["run_kind"] == "live" and (
        plan["model_binding_ref"] is None
        or any(trials_by_slot[slot_id]["model_binding_ref"] != plan["model_binding_ref"] for slot_id in trials_by_slot)
    ))
    edge_reports: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    groups_by_id = {group["group_id"]: group for group in spec["groups"]}
    slots_by_key = {(slot["group_id"], slot["case_id"], slot["repetition"]): slot for slot in run["slots"]}
    case_ids = [item["case_id"] for item in plan["suite_summary"]["cases"]]

    for edge in spec["edges"]:
        baseline_id, experiment_id = edge["baseline_group"], edge["experiment_group"]
        reasons = _valid_factor_edge(spec, edge)
        if binding_mismatch:
            reasons.append("trial_model_binding_mismatch")
        if run["run_kind"] == "live" and plan["model_binding_ref"] is None:
            reasons.append("model_binding_unavailable_for_live_comparison")
        comparison_groups = {baseline_id, experiment_id}
        source_mismatch = any(
            trial["group_id"] in comparison_groups
            and trial["source_revision"] != groups_by_id[trial["group_id"]]["source_revision"]
            for trial in trials_by_slot.values()
        )
        if source_mismatch:
            reasons.append("source_revision_mismatch")
        planned_pairs = len(case_ids) * spec["repeats"]
        pairs: list[dict[str, Any]] = []
        any_pair_incomplete = False
        case_rows: dict[str, list[tuple[dict[str, Any] | None, dict[str, Any] | None]]] = {case_id: [] for case_id in case_ids}
        regressions: list[dict[str, Any]] = []
        improvements: list[dict[str, Any]] = []
        for case_id in case_ids:
            for repetition in range(1, spec["repeats"] + 1):
                left_slot = slots_by_key.get((baseline_id, case_id, repetition))
                right_slot = slots_by_key.get((experiment_id, case_id, repetition))
                left = trials_by_slot.get(left_slot["slot_id"]) if left_slot else None
                right = trials_by_slot.get(right_slot["slot_id"]) if right_slot else None
                case_rows[case_id].append((left, right))
                left_pass = left["grader"]["passed"] if is_scorable(left) else None
                right_pass = right["grader"]["passed"] if is_scorable(right) else None
                pair = {
                    "case_id": case_id, "repetition": repetition,
                    "baseline_slot": left_slot["slot_id"] if left_slot else None,
                    "experiment_slot": right_slot["slot_id"] if right_slot else None,
                    "baseline_slot_status": left_slot["status"] if left_slot else "missing_slot",
                    "experiment_slot_status": right_slot["status"] if right_slot else "missing_slot",
                    "baseline_grader_passed": left_pass,
                    "experiment_grader_passed": right_pass,
                    "baseline_exclusion_reason": trial_eligibility(left)[1],
                    "experiment_exclusion_reason": trial_eligibility(right)[1],
                    "baseline_strict_success": _strict_success(left),
                    "experiment_strict_success": _strict_success(right),
                    "baseline_evidence": _evidence(left),
                    "experiment_evidence": _evidence(right),
                }
                if (left_slot is None or right_slot is None
                        or left_slot["status"] != "completed"
                        or right_slot["status"] != "completed"
                        or not is_scorable(left) or not is_scorable(right)):
                    any_pair_incomplete = True
                pairs.append(pair)
                if left_pass is True and right_pass is False:
                    regressions.append(pair)
                    review_queue.append({"kind": "grader_regression", "edge": f"{baseline_id}->{experiment_id}", "case_id": case_id, "repetition": repetition, "pair": pair})
                elif left_pass is False and right_pass is True:
                    improvements.append(pair)
                    review_queue.append({"kind": "grader_improvement", "edge": f"{baseline_id}->{experiment_id}", "case_id": case_id, "repetition": repetition, "pair": pair})
                for trial, label in ((left, "baseline"), (right, "experiment")):
                    if trial is not None and trial["memory_retrieval_enabled"]:
                        memory = trial["memory_evidence"]
                        if not memory["retrieval_occurred"] or memory["failure_categories"]:
                            review_queue.append({
                                "kind": "memory_condition_anomaly", "edge": f"{baseline_id}->{experiment_id}",
                                "case_id": case_id, "repetition": repetition, "group": trial["group_id"],
                                "retrieval_occurred": memory["retrieval_occurred"],
                                "failure_categories": memory["failure_categories"],
                                "evidence": _evidence(trial),
                            })
        per_case: dict[str, Any] = {}
        for case_id, paired in case_rows.items():
            valid_pairs = [(left, right) for left, right in paired
                           if is_scorable(left) and is_scorable(right)]
            left_scored = [left for left, _ in valid_pairs]
            right_scored = [right for _, right in valid_pairs]
            left_passes = sum(item["grader"]["passed"] is True for item in left_scored)
            right_passes = sum(item["grader"]["passed"] is True for item in right_scored)
            left_rate = left_passes / len(left_scored) if left_scored else None
            right_rate = right_passes / len(right_scored) if right_scored else None
            def paired_total(items, field):
                values = [item["agent"][field] for item in items if type(item["agent"][field]) is int]
                return sum(values) if values and len(values) == len(items) else None
            left_agent, right_agent = left_scored, right_scored
            left_durations = [item["agent"]["duration_ms"] for item in left_agent if type(item["agent"]["duration_ms"]) is int]
            right_durations = [item["agent"]["duration_ms"] for item in right_agent if type(item["agent"]["duration_ms"]) is int]
            durations_complete = bool(valid_pairs) and len(left_durations) == len(right_durations) == len(valid_pairs)
            left_median = median(left_durations) if durations_complete else None
            right_median = median(right_durations) if durations_complete else None
            per_case[case_id] = {
                "baseline_grader": {"passed": left_passes, "denominator": len(left_scored), "planned": spec["repeats"]},
                "experiment_grader": {"passed": right_passes, "denominator": len(right_scored), "planned": spec["repeats"]},
                "pass_rate_percentage_point_delta": (right_rate - left_rate) * 100 if left_rate is not None and right_rate is not None else None,
                "cost_delta_usd": paired_cost_delta(left_agent, right_agent, spec["price_snapshot"]),
                "input_token_delta": (paired_total(right_agent, "input_tokens") - paired_total(left_agent, "input_tokens")) if paired_total(right_agent, "input_tokens") is not None and paired_total(left_agent, "input_tokens") is not None else None,
                "output_token_delta": (paired_total(right_agent, "output_tokens") - paired_total(left_agent, "output_tokens")) if paired_total(right_agent, "output_tokens") is not None and paired_total(left_agent, "output_tokens") is not None else None,
                "agent_median_duration_ms": {"baseline": left_median, "experiment": right_median, "delta": (right_median - left_median) if left_median is not None and right_median is not None else None},
                "grader_median_duration_ms": {
                    "baseline": median([item["grader"]["duration_ms"] for item in left_scored]) if left_scored and all(type(item["grader"]["duration_ms"]) is int for item in left_scored) else None,
                    "experiment": median([item["grader"]["duration_ms"] for item in right_scored]) if right_scored and all(type(item["grader"]["duration_ms"]) is int for item in right_scored) else None,
                },
            }
        if len(pairs) != planned_pairs:
            reasons.append("paired_slot_coverage_mismatch")
        if any_pair_incomplete:
            reasons.append("one_or_more_paired_samples_not_scorable")
        for slot in run["slots"]:
            if slot["group_id"] in comparison_groups and slot["status"] != "completed":
                review_queue.append({"kind": "incomplete_slot", "edge": f"{baseline_id}->{experiment_id}", "slot_id": slot["slot_id"], "status": slot["status"], "error_kind": slot["error_kind"]})
        edge_reports.append({
            "edge_id": f"{baseline_id}->{experiment_id}",
            "baseline_group": baseline_id, "experiment_group": experiment_id,
            "allowed_changes": edge["allowed_changes"],
            "conditions_match": not [reason for reason in reasons if reason not in {
                "paired_slot_coverage_mismatch", "one_or_more_paired_samples_not_scorable"}],
            "comparable": not reasons,
            "scorable_pairs": sum(pair["baseline_grader_passed"] is not None
                                  and pair["experiment_grader_passed"] is not None for pair in pairs),
            "incomparability_reasons": reasons,
            "planned_pairs": planned_pairs,
            "covered_pairs": sum(pair["baseline_slot"] is not None and pair["experiment_slot"] is not None for pair in pairs),
            "per_case": per_case,
            "grader_regressions": regressions,
            "grader_improvements": improvements,
            "paired_slots": pairs,
            "interpretation_limit": "三次重复只作为观察结果；不宣称统计显著性或推及其他题集、模型与记忆学习。",
        })

    for slot in run["slots"]:
        trial = trials_by_slot.get(slot["slot_id"])
        if trial is not None and not is_scorable(trial):
            review_queue.append({"kind": "excluded_capability_sample", "slot_id": slot["slot_id"],
                                 "reason": trial_eligibility(trial)[1], "evidence": _evidence(trial)})
        if is_scorable(trial) and trial["agent"]["stop_reason"] == "text" and trial["grader"]["passed"] is False:
            review_queue.append({"kind": "agent_text_grader_failed", "slot_id": slot["slot_id"], "evidence": _evidence(trial)})
        if is_scorable(trial) and trial["grader"]["passed"] is True and not _strict_success(trial):
            review_queue.append({"kind": "grader_pass_strict_success_failed", "slot_id": slot["slot_id"], "evidence": _evidence(trial)})

    scorable_count = sum(is_scorable(trial) for trial in trials_by_slot.values())
    complete = (
        run["status"] == "completed"
        and all(slot["status"] == "completed" for slot in run["slots"])
        and scorable_count == len(run["slots"])
    )
    report = {
        "schema_version": 1,
        "report_rules_version": REPORT_RULES_VERSION,
        "frozen_metrics_rules_version": spec["metrics_rules_version"],
        "source_evidence": {
            "run_id": run["run_id"],
            "relative_directory": ".",
            "files": {name: sha256_bytes(_read_regular(root / name, MAX_COMPARISON_RUN_BYTES))
                      for name in ("comparison-run.json", "comparison-plan.json", "comparison-spec.json", "suite.json")},
        },
        "comparison_id": spec["comparison_id"],
        "comparison_version": spec["version"],
        "run_id": run["run_id"],
        "run_kind": run["run_kind"],
        "run_status": run["status"],
        "batch_complete": all(slot["status"] in {"completed", "infrastructure_error", "interrupted"} for slot in run["slots"]),
        "comparison_complete": complete and run["status"] == "completed" and all(edge["comparable"] for edge in edge_reports),
        "suite": plan["suite_summary"],
        "planned_slots": len(run["slots"]),
        "terminal_slots": sum(slot["status"] in {"completed", "infrastructure_error", "interrupted"} for slot in run["slots"]),
        "groups": groups,
        "edges": edge_reports,
        "recovery_success_rate": None,
        "recovery_note": "编码比较不适用；可靠性历史结果单独引用，不在本比较中重算。",
        "price_snapshot": spec["price_snapshot"],
        "manual_review_queue": review_queue,
        "limitations": [
            "固定 coding-benchmark@1.1 四题与本次模型绑定。",
            "Memory 结论仅适用于冻结语义种子的自动摘要检索，不代表跨任务学习或其他记忆能力。",
            "所有原始样本均保留；基础设施失败只排除能力分母，不删除证据。",
            "组用量与耗时含失败尝试，仅作原始观测；能力差异只使用双方均可评分的配对。",
            "缺失值保持 null；价格估算不代表 provider 的精确账单。",
            "每题三次重复仅形成描述性观察，不宣称统计显著。",
        ],
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    def display(value: Any) -> str:
        return "null" if value is None else str(value)

    source_directory = report["source_evidence"]["relative_directory"]
    lines = [
        f"# 比较报告：{report['comparison_id']}", "",
        f"- 批次：`{report['run_id']}`（{report['run_status']}）",
        f"- 完整槽位：{report['terminal_slots']} / {report['planned_slots']}",
        f"- 原始证据目录：`{source_directory}`（相对于本报告目录）",
        f"- 报告规则：{report['report_rules_version']}；能力比较完成：{'是' if report['comparison_complete'] else '否'}",
        "- 原始 token/失败耗时包含保守估算，只作诊断，不代表正常能力代价或实际账单。",
        f"- 题集：`{report['suite']['suite_id']}@{report['suite']['version']}`",
        "- 恢复成功率：`null`（编码比较不适用）", "",
        "## 组汇总", "",
        "| 组 | grader 通过 | 严格成功 | 工具调用 | 输入 token | 输出 token | Agent median ms | grader median ms | 成本 USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group_id, group in report["groups"].items():
        accept = group["independent_acceptance"]
        strict = group["strict_task_success"]
        lines.append(
            f"| `{group_id}` | {accept['passed']} / {accept['denominator']} | "
            f"{strict['passed']} / {strict['denominator']} | "
            f"{display(group['tool_calls']['total'])} | {display(group['input_tokens']['total'])} | "
            f"{display(group['output_tokens']['total'])} | {display(group['agent_duration_ms']['median'])} | "
            f"{display(group['grader_duration_ms']['median'])} | {display(group['cost_usd'])} |"
        )
    lines.extend(["", "## 样本资格", "", "| 组 | 原始 grader 通过 | 有效能力样本 | 基础设施异常 |", "|---|---:|---:|---:|"])
    for group_id, group in report["groups"].items():
        raw = group["raw_grader"]
        lines.append(f"| `{group_id}` | {raw['passed']} / {raw['denominator']} | {group['scorable_trials']} | {group['infrastructure_errors']} |")
    for edge in report["edges"]:
        lines.extend(["", f"## {edge['edge_id']}", "", f"可比较：{'是' if edge['comparable'] else '否'}"])
        lines.append(f"有效配对：{edge['scorable_pairs']} / {edge['planned_pairs']}；合同条件一致：{'是' if edge['conditions_match'] else '否'}")
        if edge["incomparability_reasons"]:
            lines.append("不可比较原因：" + ", ".join(f"`{reason}`" for reason in edge["incomparability_reasons"]))
        lines.extend([
            "", "| 题目 | 基线通过 | 实验通过 | 通过率变化（百分点） | 输入 token 差 | 输出 token 差 | Agent median 差 ms |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for case_id, item in edge["per_case"].items():
            left, right = item["baseline_grader"], item["experiment_grader"]
            lines.append(
                f"| `{case_id}` | {left['passed']} / {left['denominator']} | "
                f"{right['passed']} / {right['denominator']} | {display(item['pass_rate_percentage_point_delta'])} | "
                f"{display(item['input_token_delta'])} | {display(item['output_token_delta'])} | "
                f"{display(item['agent_median_duration_ms']['delta'])} |"
            )
        lines.extend(["", "配对槽位证据："])
        for pair in edge["paired_slots"]:
            left_path = pair["baseline_evidence"]["artifacts"]["diff"] if pair["baseline_evidence"] else "缺失"
            right_path = pair["experiment_evidence"]["artifacts"]["diff"] if pair["experiment_evidence"] else "缺失"
            left_link = f"[diff](<{source_directory}/{left_path}>)" if pair["baseline_evidence"] else left_path
            right_link = f"[diff](<{source_directory}/{right_path}>)" if pair["experiment_evidence"] else right_path
            lines.append(
                f"- `{pair['case_id']}` 重复 {pair['repetition']}："
                f"基线 `{display(pair['baseline_grader_passed'])}`（{left_link}），"
                f"实验 `{display(pair['experiment_grader_passed'])}`（{right_link}）。"
            )
        lines.append("\n" + edge["interpretation_limit"])
    lines.extend(["", "## 复核队列", ""])
    if report["manual_review_queue"]:
        for item in report["manual_review_queue"]:
            lines.append(f"- `{item['kind']}`：{item.get('slot_id') or item.get('edge') or item.get('group')}")
    else:
        lines.append("- 无自动标记项目。")
    lines.extend(["", "## 限制", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines).rstrip() + "\n"


def write_derived_reports(run_dir: str | Path, report: dict[str, Any], *, output: str | Path | None = None) -> tuple[Path, Path]:
    """Atomically refresh only the reproducible report files, never raw evidence."""
    root = Path(run_dir).expanduser().resolve(strict=True)
    if output is not None:
        destination = Path(output).expanduser().absolute()
        if destination.resolve(strict=False).is_relative_to(root):
            raise ValueError("derived report output must be outside the source archive")
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        report = {**report, "source_evidence": {**report["source_evidence"],
                  "relative_directory": os.path.relpath(root, destination.resolve())}}
        root = destination
    payloads = {
        root / "report.json": json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        root / "report.md": render_markdown(report).encode("utf-8"),
    }
    written: list[Path] = []
    for path, data in payloads.items():
        descriptor, temporary = tempfile.mkstemp(prefix=".comparison-report-", dir=root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            written.append(path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return written[0], written[1]


__all__ = ["build_comparison_report", "render_markdown", "write_derived_reports"]
