"""Frozen reliability suite orchestration and ordered, resumable slot ledger."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from typing import Any

from mini_agent.evaluation.reliability_schema import (
    LIVE_SCENARIO_IDS, ReliabilityScenario, ReliabilitySuite, canonical_json,
    load_suite, sha256,
)
from mini_agent.evaluation.runner import (
    MAX_AGENT_LOG_BYTES, _atomic_write, _process_environment, _read_limited, _redact, _run_child,
)


# Updated only after review and a version bump of the frozen reliability suite.
PINNED_SUITE_FINGERPRINT = "376170c1721cf6862c2c9faa3bc99195ff2c7a008afc0e7e89910bcbdd3502db"
MAX_RELIABILITY_SLOTS = 64
MAX_WORKER_RESULT_BYTES = 256 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def reliability_plan(suite: ReliabilitySuite, *, run_kind: str, repeats: int | None = None) -> list[dict[str, Any]]:
    if run_kind == "fixture":
        count = suite.offline_repeats if repeats is None else repeats
        if count != suite.offline_repeats:
            raise ValueError("离线不变量重复次数由冻结 suite 固定")
        selected = tuple(item.scenario_id for item in suite.scenarios)
    elif run_kind == "live":
        count = suite.default_repeats if repeats is None else repeats
        if count != 3:
            raise ValueError("v1.0 live 运行固定为七个场景、每场景三次")
        selected = suite.live_scenario_ids
    else:
        raise ValueError("run_kind 必须是 fixture 或 live")
    if count * len(selected) > MAX_RELIABILITY_SLOTS:
        raise ValueError("reliability slot 数超过上限")
    slots: list[dict[str, Any]] = []
    for scenario_id in selected:
        scenario = next(item for item in suite.scenarios if item.scenario_id == scenario_id)
        parameters = scenario.fault.get("parameters", {})
        variant_values = parameters.get("variant_values", {}) if isinstance(parameters, dict) else {}
        variants: list[dict[str, str]] = [{}]
        for axis, values in variant_values.items():
            variants = [dict(current, **{axis: value}) for current in variants for value in values]
        if run_kind == "live" and scenario_id == "mcp-disconnect":
            variants = [variant for variant in variants if variant.get("transport", "stdio") == "stdio"]
        for variant in variants:
            variant_id = "-".join(f"{key}-{value}" for key, value in sorted(variant.items())) or "default"
            for repetition in range(1, count + 1):
                slots.append({
                    "slot_id": f"{scenario_id}-{variant_id}-r{repetition:02d}",
                    "scenario_id": scenario_id, "variant": variant,
                    "repetition": repetition, "run_kind": run_kind,
                    "status": "not_run", "trial_id": None,
                    "trial_path": None, "error_kind": None,
                })
    if len(slots) > MAX_RELIABILITY_SLOTS:
        raise ValueError("parameter variants exceed reliability slot limit")
    return slots


def _atomic_json(path: Path, value: Any, *, limit: int = MAX_WORKER_RESULT_BYTES) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("reliability_artifact_too_large")
    _atomic_write(path, raw)


def _scenario_entry(suite: ReliabilitySuite, scenario_id: str) -> dict[str, str]:
    return next(item for item in suite.scenario_entries if item["scenario_id"] == scenario_id)


def _copy_frozen_materials(suite: ReliabilitySuite, target: Path) -> None:
    frozen = target / "frozen"
    frozen.mkdir(mode=0o700)
    _atomic_write(frozen / "suite.json", (suite.suite_path).read_bytes())
    support_rel = "support"
    ignore_runtime_caches = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(suite.suite_path.parent / support_rel, frozen / support_rel,
                    symlinks=False, ignore=ignore_runtime_caches)
    for entry in suite.scenario_entries:
        source = suite.suite_path.parent / entry["path"]
        destination = frozen / entry["path"]
        shutil.copytree(source, destination, symlinks=False, ignore=ignore_runtime_caches)


def _live_binding(scenario: ReliabilityScenario) -> tuple[dict[str, Any], tuple[str, ...]]:
    from mini_agent.evaluation.runner import _load_live_binding
    from mini_agent.evaluation.schema import Case

    case = Case(
        case_id=scenario.scenario_id, version=scenario.version, task=scenario.task,
        fixture_dir=scenario.initial_dir, grader_script="grader.py",
        agent_timeout_seconds=scenario.budget["wall_seconds"],
        grader_timeout_seconds=scenario.budget["grader_seconds"],
        max_rounds=scenario.budget["max_rounds"],
        allowed_tools=tuple(x for x in scenario.allowed_tools if x in {
            "read_file", "list_dir", "grep", "write_file", "edit_file", "calculate",
        }) or ("read_file",),
        authorized_tools=tuple(x for x in scenario.allowed_tools if x in {
            "read_file", "list_dir", "grep", "write_file", "edit_file", "calculate",
        }),
        model_profile=None,
    )
    return _load_live_binding(case)


def _empty_infra_result(
    suite: ReliabilitySuite, scenario: ReliabilityScenario, slot: dict[str, Any],
    trial_id: str, kind: str,
) -> dict[str, Any]:
    entry = _scenario_entry(suite, scenario.scenario_id)
    return {
        "format": "mini_agent.reliability", "schema_version": 1,
        "trial_id": trial_id, "suite_id": suite.suite_id, "suite_version": suite.version,
        "suite_sha256": suite.suite_sha256, "scenario_id": scenario.scenario_id,
        "scenario_sha256": entry["materials_sha256"], "run_kind": slot["run_kind"],
        "variant": dict(slot.get("variant", {})), "repetition": slot["repetition"], "status": "infrastructure_error",
        "started_at": _now(), "ended_at": _now(), "fault_status": "not_triggered",
        "fault_events": [], "invariant_status": "incomplete", "invariants": [],
        "recovery_status": "incomplete", "recovery_steps": [], "grader_passed": None,
        "agent_stop_reason": None, "agent_terminal_status": None,
        "cleanup_complete": False, "cleanup_issue": "worker_not_started",
        "infrastructure_error": kind[:128], "source_consistent": True,
        "phases": {"preflight": {"started_at": _now(), "ended_at": _now(), "usage": None}},
        "artifacts": {"evidence": []}, "manual_review": [], "feedback_events": [],
        "state_summary": {}, "trace_summary": {}, "diff_patch": "",
    }


def _read_worker_result(stdout_path: Path, trial_dir: Path) -> dict[str, Any] | None:
    raw = _read_limited(stdout_path, MAX_WORKER_RESULT_BYTES)
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    return result


def _redact_artifact_value(value: Any, redactions: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact(value, redactions)
    if isinstance(value, list):
        return [_redact_artifact_value(item, redactions) for item in value]
    if isinstance(value, dict):
        return {
            _redact(str(key), redactions): _redact_artifact_value(item, redactions)
            for key, item in value.items()
        }
    return value


def _redact_trial_artifacts(trial_dir: Path, redactions: tuple[str, ...]) -> None:
    """Scrub private live configuration values from generated artifacts before publication."""
    if not redactions:
        return
    for path in trial_dir.rglob("*"):
        if not path.is_file() or path.relative_to(trial_dir).parts[0] in {"workspace", "frozen"}:
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        safe = _redact(text, redactions)
        if safe != text:
            _atomic_write(path, safe.encode("utf-8"))


def _build_worker_request(
    *, suite: ReliabilitySuite, scenario: ReliabilityScenario, slot: dict[str, Any],
    trial_id: str, run_root: Path, trial_dir: Path,
    live_ref: dict[str, Any] | None, code_revision: str | None,
    runtime_fingerprint_value: str,
) -> dict[str, Any]:
    entry = _scenario_entry(suite, scenario.scenario_id)
    request = {
        "format": "mini_agent.reliability_request", "schema_version": 1,
        "trial_id": trial_id, "suite_id": suite.suite_id,
        "suite_version": suite.version, "suite_sha256": suite.suite_sha256,
        "scenario": scenario.to_dict(), "scenario_sha256": entry["materials_sha256"],
        "scenario_directory": str(run_root / "frozen" / entry["path"]),
        "workspace": str(trial_dir / "workspace"), "run_kind": slot["run_kind"],
        "repetition": slot["repetition"], "model_binding_ref": live_ref,
        "code_revision": code_revision, "runtime_fingerprint": runtime_fingerprint_value,
        "session_root": str(trial_dir / "sessions"),
        "crash_meta_path": str(trial_dir / "crash-meta.json"),
        "variant": slot.get("variant", {}),
    }
    if request["variant"]:
        params = request["scenario"]["fault"]["parameters"]
        params.pop("variant_values", None)
        params.update(request["variant"])
    return request


def run_reliability(
    suite: ReliabilitySuite | str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *, run_kind: str, live_confirmed: bool = False, repeats: int | None = None,
) -> Path:
    if not isinstance(suite, ReliabilitySuite):
        suite = load_suite(suite)
    if run_kind == "live" and not live_confirmed:
        raise ValueError("真实可靠性评测必须显式传 --live")
    if run_kind == "live":
        # Calibrate the same behavioral and public checks used after each trial
        # before resolving credentials or making the first provider request.
        validate_reliability(suite.suite_path)
    slots = reliability_plan(suite, run_kind=run_kind, repeats=repeats)
    from mini_agent.evaluation.benchmark import runtime_fingerprint
    from mini_agent.evaluation.runner import _code_revision
    frozen_revision = _code_revision()
    frozen_runtime = runtime_fingerprint()
    root = Path(output_dir).expanduser().absolute()
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700, exist_ok=False)
    _copy_frozen_materials(suite, root)
    run_id = str(uuid.uuid4())
    ledger: dict[str, Any] = {
        "format": "mini_agent.reliability_run", "schema_version": 1,
        "suite_run_id": run_id, "suite_id": suite.suite_id, "suite_version": suite.version,
        "suite_sha256": suite.suite_sha256, "run_kind": run_kind,
        "repeats": repeats or (suite.offline_repeats if run_kind == "fixture" else suite.default_repeats),
        "planned_trials": len(slots), "started_at": _now(), "updated_at": _now(),
        "status": "running", "code_revision": frozen_revision, "runtime_fingerprint": frozen_runtime,
        "model_binding_ref": None, "slots": slots, "manual_review": [],
    }
    _atomic_json(root / "suite-run.json", ledger)
    workspace_root = Path(__file__).resolve().parents[3]
    worker_home = Path(tempfile.mkdtemp(prefix="mini-agent-reliability-home-"))
    (worker_home / "tmp").mkdir(mode=0o700)
    env = _process_environment(worker_home, include_proxy=run_kind == "live")
    overall_started = time.monotonic()
    try:
        live_ref = None
        live_redactions: tuple[str, ...] = ()
        preflight_error = None
        if run_kind == "live":
            try:
                bindings = [_live_binding(next(x for x in suite.scenarios if x.scenario_id == sid))
                            for sid in suite.live_scenario_ids]
                references = [item[0] for item in bindings]
                if any(item != references[0] for item in references[1:]):
                    raise ValueError("live model binding drift")
                live_ref = references[0]
                live_redactions = bindings[0][1]
                ledger["model_binding_ref"] = live_ref
            except Exception as error:
                preflight_error = type(error).__name__
        current_index = -1
        try:
            for current_index, slot in enumerate(slots):
                scenario = next(item for item in suite.scenarios if item.scenario_id == slot["scenario_id"])
                trial_id = str(uuid.uuid4())
                trial_rel = Path("trials") / f"trial-{scenario.scenario_id}-{trial_id[:8]}"
                trial_dir = root / trial_rel
                trial_dir.mkdir(parents=True, mode=0o700)
                slot.update({"status": "running", "trial_id": trial_id, "trial_path": trial_rel.as_posix()})
                ledger["updated_at"] = _now()
                _atomic_json(root / "suite-run.json", ledger)
                source_before_ok = (
                    _code_revision() == frozen_revision and runtime_fingerprint() == frozen_runtime
                )
                binding_before_ok = True
                if run_kind == "live" and not preflight_error:
                    try:
                        binding_before_ok = _live_binding(scenario)[0] == live_ref
                    except Exception:
                        binding_before_ok = False
                slot_error = preflight_error or (
                    "source_changed_before_trial" if not source_before_ok else
                    "model_binding_changed_before_trial" if not binding_before_ok else None
                )
                if slot_error:
                    result = _empty_infra_result(suite, scenario, slot, trial_id, slot_error)
                    result.update({
                        "code_revision": frozen_revision,
                        "code_revision_end": _code_revision(),
                        "runtime_fingerprint": frozen_runtime,
                        "runtime_fingerprint_end": runtime_fingerprint(),
                        "model_binding_ref": live_ref,
                        "source_consistent": source_before_ok and binding_before_ok,
                    })
                    _atomic_json(trial_dir / "trial.json", result)
                    for name, content in {
                        "fault-events.json": [], "feedback-events.json": [],
                        "state-summary.json": {"status": "not_started"},
                        "trace-summary.json": {"status": "not_started"},
                        "recovery-steps.json": [], "agent.log": "", "grader.log": "", "diff.patch": "",
                    }.items():
                        data = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
                        _atomic_write(trial_dir / name, data.encode("utf-8"))
                    slot.update({"status": "infrastructure_error", "error_kind": slot_error})
                    _atomic_json(root / "suite-run.json", ledger)
                    continue
                request = _build_worker_request(
                    suite=suite, scenario=scenario, slot=slot, trial_id=trial_id,
                    run_root=root, trial_dir=trial_dir, live_ref=live_ref,
                    code_revision=frozen_revision, runtime_fingerprint_value=frozen_runtime,
                )
                scenario_directory = Path(request["scenario_directory"])
                initial = scenario_directory / scenario.initial_dir
                shutil.copytree(initial, trial_dir / "workspace", symlinks=False)
                request_path = trial_dir / "request.json"
                _atomic_json(request_path, request)
                stdout_path, stderr_path = trial_dir / "worker.stdout", trial_dir / "worker.stderr"
                worker_result = _run_child(
                    [sys.executable, "-m", "mini_agent.evaluation.reliability_worker", str(request_path)],
                    cwd=workspace_root, env=env,
                    timeout=scenario.budget["wall_seconds"] + scenario.budget["grader_seconds"] + 10,
                    stdout_path=stdout_path, stderr_path=stderr_path,
                )
                recovery_worker_result = None
                result = None
                if (run_kind == "live" and scenario.scenario_id == "crash-after-handler"
                        and worker_result.get("returncode") == 86
                        and (trial_dir / "crash-meta.json").is_file()):
                    remaining_seconds = max(
                        0.25, scenario.budget["wall_seconds"] - worker_result.get("duration_ms", 0) / 1000,
                    )
                    recovery_stdout = trial_dir / "recovery.stdout"
                    recovery_stderr = trial_dir / "recovery.stderr"
                    recovery_worker_result = _run_child(
                        [sys.executable, "-m", "mini_agent.evaluation.reliability_worker",
                         "--resume-live-crash", str(request_path)],
                        cwd=workspace_root, env=env,
                        timeout=remaining_seconds + scenario.budget["grader_seconds"] + 10,
                        stdout_path=recovery_stdout, stderr_path=recovery_stderr,
                    )
                    result = _read_worker_result(recovery_stdout, trial_dir)
                    first_log = f"initial_worker_exit={worker_result.get('returncode')}\n"
                    second_log = _read_limited(recovery_stderr, MAX_AGENT_LOG_BYTES)
                    _atomic_write(trial_dir / "agent.log", first_log.encode("utf-8"))
                    _atomic_write(trial_dir / "grader.log", second_log.encode("utf-8"))
                    if result is not None and result.get("format") == "mini_agent.reliability":
                        result.setdefault("phases", {})["crash_worker"] = {
                            "exit_code": worker_result.get("returncode"),
                            "duration_ms": worker_result.get("duration_ms"),
                            "cleanup_complete": worker_result.get("cleanup_complete"),
                            "recovery_duration_ms": recovery_worker_result.get("duration_ms"),
                        }
                        result["cleanup_complete"] = bool(
                            worker_result.get("cleanup_complete")
                            and recovery_worker_result.get("cleanup_complete")
                            and result.get("cleanup_complete")
                        )
                        if not result["cleanup_complete"]:
                            result["cleanup_issue"] = "crash_or_recovery_worker_cleanup_incomplete"
                else:
                    _atomic_write(trial_dir / "agent.log", _read_limited(stdout_path, MAX_AGENT_LOG_BYTES).encode("utf-8"))
                    _atomic_write(trial_dir / "grader.log", _read_limited(stderr_path, MAX_AGENT_LOG_BYTES).encode("utf-8"))
                    result = _read_worker_result(stdout_path, trial_dir)
                if result is None or result.get("format") != "mini_agent.reliability":
                    diagnostic = result.get("diagnostic") if isinstance(result, dict) else None
                    crashed_after_handler = (
                        run_kind == "live" and scenario.scenario_id == "crash-after-handler"
                        and worker_result.get("returncode") == 86
                    )
                    safe_kind = (
                        result.get("infrastructure_error", "invalid_worker_result")
                        if isinstance(result, dict) else
                        ("worker_timeout" if worker_result.get("timed_out") else
                         "recovery_worker_failed" if crashed_after_handler else "invalid_worker_result")
                    )
                    result = _empty_infra_result(suite, scenario, slot, trial_id,
                                                 str(safe_kind)[:128])
                    result["cleanup_issue"] = "worker_cleanup_unconfirmed"
                    result["state_summary"]["worker_started"] = True
                    result["state_summary"]["diagnostic"] = diagnostic
                    marker_path = trial_dir / "worker-stage.json"
                    if marker_path.is_file() and marker_path.stat().st_size <= 64 * 1024:
                        marker = json.loads(marker_path.read_text(encoding="utf-8"))
                        result["phases"]["worker"] = marker
                        if marker.get("agent_started"):
                            result["cleanup_complete"] = bool(marker.get("cleanup_complete", False))
                            result["cleanup_issue"] = None if result["cleanup_complete"] else "worker_cleanup_unconfirmed"
                            for name in ("agent_stop_reason", "agent_terminal_status", "fault_status", "fault_events"):
                                if name in marker:
                                    result[name] = marker[name]
                    if crashed_after_handler:
                        result["fault_status"] = "triggered"
                        result["fault_events"] = (
                            json.loads((trial_dir / "crash-meta.json").read_text(encoding="utf-8")).get("fault_events", [])
                            if (trial_dir / "crash-meta.json").is_file() else []
                        )
                        result["recovery_status"] = "incomplete"
                source_after_ok = (
                    _code_revision() == frozen_revision and runtime_fingerprint() == frozen_runtime
                )
                binding_after_ok = True
                if run_kind == "live" and not preflight_error:
                    try:
                        binding_after_ok = _live_binding(scenario)[0] == live_ref
                    except Exception:
                        binding_after_ok = False
                phases = result.get("phases", {})
                actual_binding_ref = None
                if isinstance(phases, dict):
                    for phase_name in ("agent", "recovery"):
                        phase = phases.get(phase_name)
                        if isinstance(phase, dict) and isinstance(phase.get("model_binding_ref"), dict):
                            actual_binding_ref = phase["model_binding_ref"]
                binding_matches = run_kind != "live" or actual_binding_ref == live_ref
                result.update({
                    "code_revision": frozen_revision,
                    "code_revision_end": _code_revision(),
                    "runtime_fingerprint": frozen_runtime,
                    "runtime_fingerprint_end": runtime_fingerprint(),
                    "model_binding_ref": actual_binding_ref if run_kind == "live" else None,
                    "source_consistent": bool(
                        result.get("source_consistent", True) and source_before_ok and source_after_ok
                        and binding_before_ok and binding_after_ok and binding_matches
                    ),
                })
                if not worker_result.get("cleanup_complete", False):
                    result["cleanup_complete"] = False
                    result["cleanup_issue"] = str(worker_result.get("cleanup_issue") or "worker_cleanup_incomplete")[:256]
                if recovery_worker_result is not None and not recovery_worker_result.get("cleanup_complete", False):
                    result["cleanup_complete"] = False
                    result["cleanup_issue"] = str(recovery_worker_result.get("cleanup_issue") or "recovery_worker_cleanup_incomplete")[:256]
                _redact_trial_artifacts(trial_dir, live_redactions)
                result = _redact_artifact_value(result, live_redactions)
                _atomic_json(trial_dir / "trial.json", result)
                # Stable companion files exist even when a driver cannot yet supply richer evidence.
                for name, fallback in (
                    ("fault-events.json", result.get("fault_events", [])),
                    ("feedback-events.json", result.get("feedback_events", [])),
                    ("state-summary.json", result.get("state_summary", {})),
                    ("trace-summary.json", result.get("trace_summary", {})),
                    ("recovery-steps.json", result.get("recovery_steps", [])),
                    ("diff.patch", result.get("diff_patch", "")),
                ):
                    if not (trial_dir / name).exists():
                        payload = fallback if isinstance(fallback, str) else json.dumps(fallback, ensure_ascii=False, indent=2)
                        _atomic_write(trial_dir / name, payload.encode("utf-8"))
                if result.get("infrastructure_error"):
                    slot.update({"status": "infrastructure_error", "error_kind": str(result["infrastructure_error"])[:128]})
                else:
                    slot.update({"status": "completed", "error_kind": None})
                ledger["updated_at"] = _now()
                _atomic_json(root / "suite-run.json", ledger)
        except (KeyboardInterrupt, SystemExit):
            if 0 <= current_index < len(slots) and slots[current_index]["status"] == "running":
                slots[current_index]["status"] = "not_run"
                slots[current_index]["error_kind"] = "interrupted"
            ledger["status"] = "interrupted"
            raise
        ledger["status"] = "completed" if all(item["status"] in {"completed", "infrastructure_error"} for item in slots) else "interrupted"
        ledger["updated_at"] = _now()
        ledger["duration_ms"] = int((time.monotonic() - overall_started) * 1000)
        _atomic_json(root / "suite-run.json", ledger)
    finally:
        shutil.rmtree(Path(env["HOME"]), ignore_errors=True)
    return root


def validate_reliability(path: str | os.PathLike[str]) -> dict[str, Any]:
    from mini_agent.evaluation.reliability_diagnostics import REQUEST_BUDGET_POLICY
    suite = load_suite(path)
    offline_slots = len(reliability_plan(suite, run_kind="fixture"))
    live_slots = len(reliability_plan(suite, run_kind="live"))
    from mini_agent.evaluation.reliability_worker import _grade_workspace, _run_public_test

    scenario_previews = []
    for item in suite.scenarios:
        entry = _scenario_entry(suite, item.scenario_id)
        scenario_dir = suite.suite_path.parent / entry["path"]
        grader_contract = json.loads((scenario_dir / "grader.json").read_text(encoding="utf-8"))
        checks = list(grader_contract["checks"])
        initial_passed = known_good_passed = None
        initial_public = known_good_public = None
        boundary_only = item.scenario_id == "permission-denied" and item.version in {"1.1", "1.2", "1.3", "1.4", "1.5"}
        if item.scenario_id in suite.live_scenario_ids and not boundary_only:
            if "task-transform-doubles" not in checks:
                raise ValueError(f"{item.scenario_id} 缺少冻结的任务 grader 检查")
            initial_passed = _grade_workspace(
                item, scenario_dir / item.initial_dir, scenario_dir / "grader.json",
            )
            known_good = scenario_dir / "known_good"
            if known_good.is_symlink() or not known_good.is_dir():
                raise ValueError(f"{item.scenario_id} 缺少已知正确任务基线")
            known_good_passed = _grade_workspace(item, known_good, scenario_dir / "grader.json")
            if initial_passed is not False or known_good_passed is not True:
                raise ValueError(f"{item.scenario_id} task grader 必须拒绝 initial 并接受 known_good")
            command = "python -m unittest discover -s tests"
            initial_public = _run_public_test(scenario_dir / item.initial_dir, command)
            known_good_public = _run_public_test(known_good, command)
            if initial_public is not False or known_good_public is not True:
                raise ValueError(f"{item.scenario_id} 完整公开验证必须拒绝 initial 并接受 known_good，且必须运行非空测试")
        scenario_previews.append({
            "scenario_id": item.scenario_id, "task": item.task,
            "allowed_tools": list(item.allowed_tools),
            "permission_rules": [dict(rule) for rule in item.permission_rules],
            "budget": dict(item.budget), "fault": dict(item.fault),
            "invariants": list(item.invariants), "recovery_steps": list(item.recovery_steps),
            "feedback_strategy": [dict(row) for row in item.feedback_strategy],
            "grader_id": item.grader_id,
            "grader_checks": checks,
            "grader_calibration": {
                "initial_passed": initial_passed, "known_good_passed": known_good_passed,
            },
            "public_test_calibration": {
                "initial_passed": initial_public, "known_good_passed": known_good_public,
            },
            "assessment": "boundary_only" if boundary_only else "recovery",
            "materials_sha256": entry["materials_sha256"],
        })
    return {
        "valid": True, "format": "mini_agent.reliability", "schema_version": 1,
        "suite_id": suite.suite_id, "version": suite.version,
        "suite_sha256": suite.suite_sha256,
        "scenario_count": len(suite.scenarios),
        "offline_slots": offline_slots,
        "live_scenarios": list(suite.live_scenario_ids),
        "live_slots": live_slots,
        "request_budget_policy": dict(REQUEST_BUDGET_POLICY),
        "scenarios": scenario_previews,
    }


__all__ = ["PINNED_SUITE_FINGERPRINT", "reliability_plan", "run_reliability", "validate_reliability"]
