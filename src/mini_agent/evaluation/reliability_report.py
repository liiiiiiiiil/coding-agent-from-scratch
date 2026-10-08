"""Read-only report reconstruction for reliability-boundaries trial artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mini_agent.evaluation.evidence import inspect_evidence
from mini_agent.evaluation.reliability_schema import (
    MAX_TRIAL_BYTES, load_suite, safe_relative, validate_reliability_result,
)


def _read_json(path: Path, limit: int = MAX_TRIAL_BYTES) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("artifact_missing_or_oversize")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact_not_object")
    return value


def _scenario_report(rows: list[dict[str, Any]], planned_slots: int, run_kind: str, *,
                     infrastructure_error_slots: int = 0, unrun_slots: int = 0,
                     incomplete_slots: int = 0) -> dict[str, Any]:
    triggered = [row for row in rows if row["fault_status"] == "triggered"]
    denominator_rows = [
        row for row in triggered
        if run_kind == "live"
        and row["source_consistent"] is True
        and row["invariant_status"] != "incomplete"
        and row["recovery_status"] not in {"incomplete", "not_triggered", "not_applicable"}
        and row["grader_passed"] is not None
        and not row["infrastructure_error"]
        and not row.get("evidence_incomplete")
    ]
    numerator = sum(
        row["invariant_status"] == "passed"
        and row["recovery_status"] in {"succeeded", "not_applicable"}
        and row["agent_stop_reason"] in {"text", "completed"}
        and row["agent_terminal_status"] == "done"
        and row["grader_passed"] is True
        and row["cleanup_complete"] is True
        for row in denominator_rows
    )
    invariant_counts = {key: sum(row["invariant_status"] == key for row in rows)
                        for key in ("passed", "failed", "incomplete")}
    recovery_counts = {key: sum(row["recovery_status"] == key for row in rows)
                       for key in ("succeeded", "failed", "not_applicable", "not_triggered", "incomplete")}
    result = {
        "planned_slots": planned_slots,
        "recorded_trials": len(rows),
        "fault_triggered": len(triggered),
        "fault_not_triggered": sum(row["fault_status"] == "not_triggered" for row in rows),
        "injection_errors": sum(row["fault_status"] == "injection_error" for row in rows),
        "invariant_status_counts": invariant_counts,
        "recovery_status_counts": recovery_counts,
        "grader_passed": sum(row["grader_passed"] is True for row in rows),
        "grader_failed": sum(row["grader_passed"] is False for row in rows),
        "grader_not_run": sum(row["grader_passed"] is None for row in rows),
        "agent_stop_reasons": _counts(row.get("agent_stop_reason") or "unknown" for row in rows),
        "cleanup_incomplete": sum(row["cleanup_complete"] is False for row in rows),
        "source_inconsistent": sum(row["source_consistent"] is False for row in rows),
        "infrastructure_errors": sum(bool(row["infrastructure_error"]) for row in rows) + infrastructure_error_slots,
        "unrun_slots": unrun_slots,
        "incomplete_slots": incomplete_slots,
        "evidence_incomplete": sum(bool(row.get("evidence_incomplete")) for row in rows),
        "task_success_assessed": run_kind == "live" and any(row["recovery_status"] != "not_applicable" for row in rows),
        "boundary_only_trials": sum(row["recovery_status"] == "not_applicable" for row in rows) if run_kind == "live" else 0,
        "task_grader_not_run": sum(row["grader_passed"] is None and
                                   not (run_kind == "live" and row["recovery_status"] == "not_applicable")
                                   for row in rows),
        "task_grader_not_applicable": sum(row["recovery_status"] == "not_applicable" for row in rows)
        if run_kind == "live" else 0,
        "recovery_success_rate": numerator / len(denominator_rows) if denominator_rows else None,
        "recovery_successes": numerator if run_kind == "live" else None,
        "recovery_denominator": len(denominator_rows),
        "recovery_denominator_exclusions": {
            "triggered_but_unscorable": len(triggered) - len(denominator_rows),
            "fixture_task_not_assessed": len(triggered) if run_kind == "fixture" else 0,
            "boundary_only": sum(row["recovery_status"] == "not_applicable" for row in triggered)
            if run_kind == "live" else 0,
            "not_triggered": sum(row["fault_status"] == "not_triggered" for row in rows),
            "infrastructure_error": sum(bool(row["infrastructure_error"]) for row in rows) + infrastructure_error_slots,
            "unrun_slots": unrun_slots,
        },
    }

    if any("completion_evidence" in row.get("state_summary", {}) for row in rows):
        result["completion_layers"] = _completion_layers(rows)
    return result


def _completion_layers(rows):
    evidence = [row.get("state_summary", {}).get("completion_evidence", {}) for row in rows
                if row.get("recovery_status") != "not_applicable"]
    return {
        "task_trials": len(evidence),
        "artifact_correct": sum(item.get("artifact_correct") is True for item in evidence),
        "current_generation_verified": sum(item.get("current_generation_verified") is True for item in evidence),
        "model_completed": sum(item.get("model_completed") is True for item in evidence),
        "artifact_verified_and_completed": sum(
            all(item.get(key) is True for key in
                ("artifact_correct", "current_generation_verified", "model_completed"))
            for item in evidence),
        "note": "Diagnostic layers only; strict recovery_success_rate remains the acceptance metric.",
    }


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def build_reliability_report(run_dir: str | Path) -> dict[str, Any]:
    """Rebuild metrics only from the frozen suite, ordered ledger, and raw results."""
    root = Path(run_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("reliability run 路径必须是目录")
    ledger = _read_json(root / "suite-run.json")
    if ledger.get("format") != "mini_agent.reliability_run" or ledger.get("schema_version") != 1:
        raise ValueError("reliability suite-run 格式无效")
    slots = ledger.get("slots")
    if not isinstance(slots, list):
        raise ValueError("reliability slots 必须是列表")
    run_kind = ledger.get("run_kind")
    if run_kind not in {"fixture", "live"}:
        raise ValueError("reliability run_kind 无效")
    # Reports must remain rebuildable for archived batches whose suite is no
    # longer the current runnable suite. The frozen copy and ledger are still
    # validated against each other; only execution enforces the current pin.
    frozen_suite = load_suite(root / "frozen" / "suite.json", require_pinned=False)
    if any(ledger.get(key) != value for key, value in (
        ("suite_id", frozen_suite.suite_id), ("suite_version", frozen_suite.version),
        ("suite_sha256", frozen_suite.suite_sha256),
    )):
        raise ValueError("reliability ledger 与冻结 suite 不匹配")
    frozen_revision = ledger.get("code_revision")
    frozen_runtime = ledger.get("runtime_fingerprint")
    frozen_model_ref = ledger.get("model_binding_ref")
    if run_kind == "live" and (
        not isinstance(frozen_revision, str) or not isinstance(frozen_runtime, str)
    ):
        raise ValueError("live reliability ledger 缺少代码来源摘要")
    if run_kind == "live" and any(
        isinstance(slot, dict) and slot.get("status") == "completed" for slot in slots
    ) and not isinstance(frozen_model_ref, dict):
        raise ValueError("completed live slots 缺少冻结模型来源摘要")
    from mini_agent.evaluation.reliability import reliability_plan
    expected_slots = reliability_plan(frozen_suite, run_kind=run_kind, repeats=ledger.get("repeats"))
    if len(slots) != len(expected_slots):
        raise ValueError("reliability slot 数与冻结计划不匹配")
    for actual, expected in zip(slots, expected_slots):
        if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in (
            ("slot_id", expected["slot_id"]), ("scenario_id", expected["scenario_id"]),
            ("variant", expected["variant"]), ("repetition", expected["repetition"]),
            ("run_kind", expected["run_kind"]),
        )):
            raise ValueError("reliability slot 与冻结顺序或参数变体不匹配")
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for slot in slots:
        sid = slot.get("scenario_id") if isinstance(slot, dict) else None
        rel = slot.get("trial_path") if isinstance(slot, dict) else None
        if slot.get("status") != "completed" or not isinstance(rel, str):
            continue
        try:
            safe_relative(rel, "trial_path")
        except ValueError:
            unreadable.append(str(rel)[:512])
            continue
        trial_dir = root / rel
        try:
            current = root
            for part in Path(rel).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("trial_path_symlink")
            if not trial_dir.is_dir() or root not in trial_dir.resolve(strict=True).parents:
                raise ValueError("trial_path_escape")
            row = validate_reliability_result(_read_json(trial_dir / "trial.json"))
            scenario = next(item for item in frozen_suite.scenarios if item.scenario_id == sid)
            scenario_entry = next(item for item in frozen_suite.scenario_entries if item["scenario_id"] == sid)
            if any(row.get(key) != expected for key, expected in (
                ("scenario_id", sid), ("trial_id", slot.get("trial_id")),
                ("suite_id", frozen_suite.suite_id), ("suite_version", frozen_suite.version),
                ("suite_sha256", frozen_suite.suite_sha256),
                ("scenario_sha256", scenario_entry["materials_sha256"]),
                ("run_kind", run_kind), ("variant", slot.get("variant")),
                ("repetition", slot.get("repetition")),
            )):
                raise ValueError("trial_slot_identity_mismatch")
            source_facts_present = all(key in row for key in (
                "code_revision", "code_revision_end", "runtime_fingerprint", "runtime_fingerprint_end",
            ))
            source_matches = source_facts_present and all(row.get(key) == expected for key, expected in (
                ("code_revision", frozen_revision), ("runtime_fingerprint", frozen_runtime),
                ("runtime_fingerprint_end", frozen_runtime),
            )) and row.get("code_revision_end") == frozen_revision
            model_matches = run_kind != "live" or row.get("model_binding_ref") == frozen_model_ref
            row["source_consistent"] = bool(
                row["source_consistent"] and source_matches and model_matches
            ) if run_kind == "live" or source_facts_present else bool(row["source_consistent"])
            evidence, errors = inspect_evidence(trial_dir, row["artifacts"].get("evidence", []))
            row = dict(row)
            row["verified_evidence"] = evidence
            row["evidence_incomplete"] = bool(errors)
            row["evidence_errors"] = errors
            rows.append(row)
            by_scenario.setdefault(sid, []).append(row)
        except Exception:
            unreadable.append(str(rel)[:512])
    planned_by_scenario: dict[str, int] = {}
    infrastructure_by_scenario: dict[str, int] = {}
    unrun_by_scenario: dict[str, int] = {}
    incomplete_by_scenario: dict[str, int] = {}
    for slot in slots:
        sid = str(slot.get("scenario_id", "unknown")) if isinstance(slot, dict) else "unknown"
        planned_by_scenario[sid] = planned_by_scenario.get(sid, 0) + 1
        status = slot.get("status") if isinstance(slot, dict) else None
        if status == "infrastructure_error":
            infrastructure_by_scenario[sid] = infrastructure_by_scenario.get(sid, 0) + 1
        elif status == "not_run":
            unrun_by_scenario[sid] = unrun_by_scenario.get(sid, 0) + 1
        elif status not in {"completed", "infrastructure_error", "not_run"}:
            incomplete_by_scenario[sid] = incomplete_by_scenario.get(sid, 0) + 1
    scenarios = {
        sid: _scenario_report(
            by_scenario.get(sid, []), planned_by_scenario.get(sid, 0), run_kind,
            infrastructure_error_slots=infrastructure_by_scenario.get(sid, 0),
            unrun_slots=unrun_by_scenario.get(sid, 0),
            incomplete_slots=incomplete_by_scenario.get(sid, 0),
        )
        for sid in sorted(planned_by_scenario)
    }
    cohorts: dict[str, Any] = {}
    for kind in ("fixture", "live"):
        selected = [row for row in rows if row["run_kind"] == kind]
        slots_selected = [slot for slot in slots if slot.get("run_kind") == kind]
        recovery_denominator = [
            row for row in selected
            if row["fault_status"] == "triggered"
            and row["source_consistent"] is True
            and row["invariant_status"] != "incomplete"
            and row["recovery_status"] not in {"incomplete", "not_triggered", "not_applicable"}
            and row["grader_passed"] is not None
            and not row["infrastructure_error"]
            and not row.get("evidence_incomplete")
        ] if kind == "live" else []
        recovery_numerator = sum(
            row["invariant_status"] == "passed"
            and row["recovery_status"] in {"succeeded", "not_applicable"}
            and row["agent_stop_reason"] in {"text", "completed"}
            and row["agent_terminal_status"] == "done"
            and row["grader_passed"] is True
            and row["cleanup_complete"] is True
            for row in recovery_denominator
        )
        cohorts[kind] = {
            "planned_slots": len(slots_selected),
            "recorded_trials": len(selected),
            "fault_triggered": sum(row["fault_status"] == "triggered" for row in selected),
            "invariant_passed": sum(row["invariant_status"] == "passed" for row in selected),
            "grader_passed": sum(row["grader_passed"] is True for row in selected),
            "infrastructure_errors": sum(bool(row["infrastructure_error"]) for row in selected)
            + sum(slot.get("status") == "infrastructure_error" for slot in slots_selected),
            "unrun_slots": sum(slot.get("status") == "not_run" for slot in slots_selected),
            "incomplete_slots": sum(slot.get("status") not in {"completed", "infrastructure_error", "not_run"}
                                    for slot in slots_selected),
            "task_success_assessed": kind == "live" and any(row["recovery_status"] != "not_applicable" for row in selected),
            "boundary_only_trials": sum(row["recovery_status"] == "not_applicable" for row in selected)
            if kind == "live" else 0,
            "recovery_successes": recovery_numerator if kind == "live" else None,
            "recovery_denominator": len(recovery_denominator),
            "recovery_success_rate": recovery_numerator / len(recovery_denominator) if recovery_denominator else None,
        }
        if any("completion_evidence" in row.get("state_summary", {}) for row in selected):
            cohorts[kind]["completion_layers"] = _completion_layers(selected)
    if frozen_suite.version == "1.0":
        # Preserve the original report contract, including its definition of
        # task_success_assessed, without reading or rewriting archived reports.
        for cohort in cohorts.values():
            cohort.pop("boundary_only_trials", None)
        for scenario in scenarios.values():
            scenario.pop("boundary_only_trials", None)
            scenario.pop("task_grader_not_applicable", None)
            scenario["recovery_denominator_exclusions"].pop("boundary_only", None)
            scenario["task_success_assessed"] = run_kind == "live"
    return {
        "format": "mini_agent.reliability_report",
        "schema_version": 1,
        "suite_id": ledger.get("suite_id"),
        "suite_version": ledger.get("suite_version"),
        "suite_sha256": ledger.get("suite_sha256"),
        "suite_run_id": ledger.get("suite_run_id"),
        "run_kind": ledger.get("run_kind"),
        "status": ledger.get("status"),
        "code_revision": frozen_revision,
        "runtime_fingerprint": frozen_runtime,
        "code_revision_consistent": all(
            row.get("code_revision") == frozen_revision
            and row.get("code_revision_end") == frozen_revision for row in rows
        ) if rows and frozen_revision is not None else None,
        "runtime_fingerprint_consistent": all(
            row.get("runtime_fingerprint") == frozen_runtime
            and row.get("runtime_fingerprint_end") == frozen_runtime for row in rows
        ) if rows and frozen_runtime is not None else None,
        "model_binding_consistent": all(
            row.get("model_binding_ref") == frozen_model_ref for row in rows
        ) if run_kind == "live" and rows else None,
        "source": "read-only reconstruction from frozen suite, ledger, and raw trials",
        "cohorts": cohorts,
        "scenarios": scenarios,
        "unreadable_trials": unreadable,
        "unrun_slots": [
            {"slot_id": item["slot_id"], "scenario_id": item["scenario_id"],
             "repetition": item["repetition"], "status": item["status"],
             "error_kind": item.get("error_kind")}
            for item in slots if item.get("status") != "completed"
        ],
        "manual_review": list(ledger.get("manual_review", [])),
    }


__all__ = ["build_reliability_report"]
