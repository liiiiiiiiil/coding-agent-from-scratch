"""Freeze and run paired coding comparisons through isolated target checkouts."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from typing import Any

from mini_agent.evaluation import benchmark
from mini_agent.evaluation.comparison_schema import (
    COMPARISON_SCHEMA_VERSION, MAX_COMPARISON_RUN_BYTES, MAX_COMPARISON_SPEC_BYTES,
    MAX_COMPARISON_TRIAL_BYTES, PINNED_V052_REVISION, ComparisonSpec,
    canonical_json, comparison_execution_order, sha256_bytes, sha256_json, validate_comparison_run,
    validate_comparison_spec, validate_comparison_trial,
)
from mini_agent.evaluation.comparison_sources import (
    COMPARISON_WORKER, REPOSITORY_ROOT, adapter_fingerprint, current_commit,
    create_private_temp_dir, environment_snapshot, preflight_source,
    require_clean_worktree, resolve_commit, source_checkout, source_snapshot,
    source_tree_fingerprint,
)
from mini_agent.evaluation.runner import (
    MAX_AGENT_LOG_BYTES, MAX_DIFF_BYTES, MAX_GRADER_LOG_BYTES, MAX_RESULT_BYTES,
    MAX_WORKER_RESULT_BYTES, _atomic_write, _build_diff, _copy_fixture,
    _prepare_grader_result, _process_environment, _read_limited, _redact,
    _run_child, _snapshot, run_frozen_grader_source,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".comparison-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_json(path: Path, value: dict[str, Any], *, limit: int = MAX_COMPARISON_RUN_BYTES) -> bytes:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(data) > limit:
        raise ValueError(f"comparison JSON exceeds {limit} bytes")
    _write_bytes(path, data)
    return data


def _read_json(path: Path, limit: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"comparison input is missing or not a regular file: {path.name}")
    if path.stat().st_size > limit:
        raise ValueError(f"comparison input exceeds {limit} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid comparison JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"comparison input must be a JSON object: {path.name}")
    return value


def _read_regular_below(root: Path, relative: str, limit: int) -> bytes:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("comparison input path cannot include symlinks")
    if not current.is_file() or current.stat().st_size > limit:
        raise ValueError("comparison input path is missing or exceeds its limit")
    return current.read_bytes()


def _read_spec(path: str | os.PathLike[str]) -> ComparisonSpec:
    raw = _read_json(Path(path).expanduser().absolute(), MAX_COMPARISON_SPEC_BYTES)
    return validate_comparison_spec(raw)


def _load_suite(spec: ComparisonSpec):
    path = REPOSITORY_ROOT / spec.value["suite"]["path"]
    if not path.resolve(strict=True).is_relative_to(REPOSITORY_ROOT.resolve()):
        raise ValueError("suite path must remain inside the repository")
    suite = benchmark.load_suite(path)
    if (suite.suite_id != spec.value["suite"]["suite_id"]
            or suite.version != spec.value["suite"]["version"]
            or suite.suite_sha256 != spec.value["suite"]["sha256"]):
        raise ValueError("ComparisonSpec suite differs from the pinned suite")
    return suite


def _semantic_memory(item: dict[str, Any]) -> dict[str, Any]:
    expected = {"schema_version", "case_id", "title", "body", "tags", "source"}
    if set(item) != expected or item.get("schema_version") != 1:
        raise ValueError("memory seed file has unknown or missing fields")
    if (not isinstance(item["case_id"], str) or not isinstance(item["title"], str)
            or not isinstance(item["body"], str) or not isinstance(item["source"], str)
            or not isinstance(item["tags"], list) or len(item["tags"]) > 8
            or not all(isinstance(tag, str) for tag in item["tags"])):
        raise ValueError("memory seed fields are invalid")
    if any(len(item[key]) > maximum for key, maximum in (("title", 120), ("body", 2000), ("source", 240))):
        raise ValueError("memory seed exceeds the MemoryStore field bounds")
    return {key: item[key] for key in ("case_id", "title", "body", "tags", "source")}


def _load_memory_materials(spec: ComparisonSpec, suite) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    repo = REPOSITORY_ROOT.resolve()
    rows: dict[str, dict[str, Any]] = {}
    summaries: list[dict[str, str]] = []
    materials = spec.value["memory_materials"]
    if [item["case_id"] for item in materials] != [item.case.case_id for item in suite.cases]:
        raise ValueError("memory materials must cover every suite case in suite order")
    for item in materials:
        path = REPOSITORY_ROOT / item["path"]
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(repo):
            raise ValueError("memory material path is unsafe")
        if path.stat().st_size > 16 * 1024:
            raise ValueError("memory material file exceeds 16 KiB")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("memory material JSON is invalid") from error
        if not isinstance(raw, dict):
            raise ValueError("memory material JSON must be an object")
        semantic = _semantic_memory(raw)
        if semantic["case_id"] != item["case_id"]:
            raise ValueError("memory material case_id mismatch")
        digest = sha256_json(semantic)
        if digest != item["semantic_sha256"]:
            raise ValueError(f"memory material digest mismatch: {item['case_id']}")
        rows[item["case_id"]] = {
            key: semantic[key] for key in ("case_id", "title", "body", "tags", "source")
        }
        summaries.append({"case_id": semantic["case_id"], "semantic_sha256": digest})
    combined = sha256_json(summaries)
    if combined != spec.value["groups"][0]["memory_material_sha256"]:
        raise ValueError("combined memory material digest mismatch")
    return rows, {"semantic_digest": combined, "materials": summaries}


def _validate_v053_spec(spec: ComparisonSpec, suite) -> None:
    raw = spec.value
    if raw["metrics_rules_version"] != "1":
        raise ValueError("v0.53 comparison fixes metrics_rules_version=1")
    if raw["suite"]["suite_id"] != "coding-benchmark" or raw["suite"]["version"] != "1.1":
        raise ValueError("v0.53 comparison fixes coding-benchmark@1.1")
    expected_ids = ["pagination-boundary", "orders-discount-receipt", "cache-expiry-regression", "config-priority-investigation"]
    if [item.case.case_id for item in suite.cases] != expected_ids:
        raise ValueError("coding-benchmark@1.1 case order differs from the v0.53 contract")
    if raw["repeats"] != 3:
        raise ValueError("v0.53 comparison fixes three repetitions per case and group")
    if [group["group_id"] for group in raw["groups"]] != ["old-off", "current-off", "current-on"]:
        raise ValueError("v0.53 groups must be ordered old-off, current-off, current-on")
    old, current_off, current_on = raw["groups"]
    if old["source_revision"] != PINNED_V052_REVISION:
        raise ValueError("old-off must use the pinned v0.52 commit")
    if current_off["source_revision"] != current_on["source_revision"]:
        raise ValueError("both current groups must use the same frozen v0.53 commit")
    if current_off["source_revision"] == old["source_revision"]:
        raise ValueError("current source must be a new v0.53 commit")
    if (old["memory_retrieval_enabled"] is not False
            or current_off["memory_retrieval_enabled"] is not False
            or current_on["memory_retrieval_enabled"] is not True):
        raise ValueError("v0.53 memory conditions must be off, off, on")
    if len({group["model_profile"] for group in raw["groups"]}) != 1:
        raise ValueError("all groups must use the same local model profile alias")
    if raw["edges"] != [
        {"baseline_group": "old-off", "experiment_group": "current-off", "allowed_changes": ["source_revision"]},
        {"baseline_group": "current-off", "experiment_group": "current-on", "allowed_changes": ["memory_retrieval_enabled"]},
    ]:
        raise ValueError("v0.53 comparison edges must be old-off→current-off and current-off→current-on")


def _load_model_binding(profile_name: str) -> tuple[dict[str, Any], dict[str, str], tuple[str, ...]]:
    from mini_agent import config
    from mini_agent.providers.catalog import ProviderCatalog

    catalog = ProviderCatalog.from_module(config)
    selected = catalog.resolve_parent_profile(profile_name)
    if selected != profile_name:
        raise ValueError("comparison model profile must resolve to its exact declared alias")
    binding = catalog.bind(selected)
    provider, profile = binding.provider, binding.profile
    endpoint_host = re.sub(r"^.*://([^/:]+).*$", r"\1", provider.endpoint).casefold()
    if (not provider.api_key or "placeholder" in provider.api_key.casefold()
            or "placeholder" in profile.model_id.casefold()
            or ".invalid" in endpoint_host or "example.invalid" in endpoint_host):
        raise ValueError("local model profile still contains template values")
    payload = {
        "providers": {
            provider.provider_id: {
                "protocol": provider.protocol, "endpoint": provider.endpoint,
                "api_key": provider.api_key, "timeout_seconds": provider.timeout_seconds,
                "extra_headers": dict(provider.extra_headers),
            },
        },
        "profiles": {
            profile.name: {
                "provider_id": profile.provider_id, "model_id": profile.model_id,
                "context_window": profile.context_window,
                "max_output_tokens": profile.max_output_tokens,
                "supports_tools": profile.supports_tools,
                "supports_streaming": profile.supports_streaming,
            },
        },
        "profile": selected,
        "parent_profile": selected,
    }
    redactions = {provider.api_key, provider.endpoint, profile.model_id}
    for header_value in provider.extra_headers.values():
        redactions.add(str(header_value))
    encoded_binding = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded_binding) > 64 * 1024:
        raise ValueError("local model binding exceeds the comparison worker limit")
    return payload, binding.reference.to_dict(), tuple(sorted((x for x in redactions if len(x) >= 3), key=len, reverse=True))


def load_fixture_responses(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    selected = Path(path) if path is not None else REPOSITORY_ROOT / "tests/fixtures/evaluation/comparison/responses.json"
    raw = _read_json(selected.absolute(), 256 * 1024)
    if set(raw) != {"schema_version", "description", "cases"} or raw["schema_version"] != 1:
        raise ValueError("comparison response fixture contract invalid")
    if not isinstance(raw["cases"], dict):
        raise ValueError("comparison response fixture cases must be an object")
    return raw["cases"]


def _wire_responses(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    call_number = 0
    for action in actions:
        if set(action) == {"final"}:
            result.append({"role": "assistant", "content": action["final"], "tool_calls": []})
        elif set(action) == {"tool", "arguments"}:
            call_number += 1
            result.append({
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": f"fixture-call-{call_number}", "type": "function",
                    "function": {
                        "name": action["tool"],
                        "arguments": json.dumps(action["arguments"], ensure_ascii=False, separators=(",", ":")),
                    },
                }],
            })
        else:
            raise ValueError("comparison response fixture action has unknown fields")
    return result


def _execution_order(suite, spec: ComparisonSpec) -> list[dict[str, Any]]:
    return comparison_execution_order(
        [item.case.case_id for item in suite.cases], spec.value["groups"], spec.value["repeats"],
    )


def _suite_summary(suite) -> dict[str, Any]:
    return benchmark.comparison_suite_summary(suite)


def _verify_source_suite(revision: str, expected_suite) -> dict[str, str]:
    with source_checkout(revision) as checkout:
        preflight = preflight_source(checkout)
        target_suite = benchmark.load_suite(
            checkout / "tests/fixtures/evaluation/benchmark/suite.json",
            require_pinned=True,
        )
        if (target_suite.suite_id != expected_suite.suite_id
                or target_suite.version != expected_suite.version
                or target_suite.suite_sha256 != expected_suite.suite_sha256):
            raise ValueError("target checkout coding suite differs from comparison suite")
        benchmark.validate_suite_baselines(target_suite)
        return {
            "runtime_fingerprint": preflight["runtime_fingerprint"],
            "suite_sha256": target_suite.suite_sha256,
            "preflight": "passed",
        }


def validate_comparison(path: str | os.PathLike[str]) -> dict[str, Any]:
    spec = _read_spec(path)
    suite = _load_suite(spec)
    _validate_v053_spec(spec, suite)
    _load_memory_materials(spec, suite)
    if spec.value["budget"] != {"max_tool_calls": 48, "max_total_tokens": 64000}:
        raise ValueError("v0.53 comparison budget must use 48 tools and 64,000 tokens")
    return {
        "valid": True,
        "comparison_id": spec.comparison_id,
        "spec_sha256": spec.digest,
        "suite": {"suite_id": suite.suite_id, "version": suite.version, "sha256": suite.suite_sha256, "cases": len(suite.cases)},
        "groups": [item["group_id"] for item in spec.value["groups"]],
        "repeats": spec.value["repeats"],
        "planned_trials": len(_execution_order(suite, spec)),
    }


def plan_comparison(
    path: str | os.PathLike[str], output: str | os.PathLike[str], *, require_binding: bool = True,
) -> Path:
    spec = _read_spec(path)
    suite = _load_suite(spec)
    _validate_v053_spec(spec, suite)
    _, memory_summary = _load_memory_materials(spec, suite)
    require_clean_worktree()
    head = current_commit()
    expected_current = spec.value["groups"][1]["source_revision"]
    if resolve_commit(expected_current) != head:
        raise ValueError("current-off revision must equal clean HEAD")
    baseline_outcomes = benchmark.validate_suite_baselines(suite)
    source_snapshots: dict[str, Any] = {}
    source_checks: dict[str, Any] = {}
    for group in spec.value["groups"]:
        group_id = group["group_id"]
        revision = resolve_commit(group["source_revision"])
        snapshot = source_snapshot(revision)
        expected_version = "0.52.0" if group_id == "old-off" else "0.53.0"
        if snapshot["package_version"] != expected_version:
            raise ValueError(f"{group_id} source package version must be {expected_version}")
        source_snapshots[group_id] = snapshot
        if revision not in source_checks:
            source_checks[revision] = _verify_source_suite(revision, suite)
        if source_checks[revision]["runtime_fingerprint"] != snapshot["runtime_fingerprint"]:
            raise ValueError("source fingerprint changed during plan preflight")
    adapter_hash = adapter_fingerprint()
    if require_binding:
        binding_payload, binding_ref, _redactions = _load_model_binding(spec.value["groups"][0]["model_profile"])
        _ = binding_payload, _redactions
    else:
        binding_ref = None
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": str(uuid.uuid4()),
        "comparison_id": spec.comparison_id,
        "spec": spec.value,
        "spec_sha256": spec.digest,
        "suite_summary": _suite_summary(suite),
        "source_snapshots": source_snapshots,
        "adapter_sha256": adapter_hash,
        "environment": environment_snapshot(),
        "model_binding_ref": binding_ref,
        "execution_order": _execution_order(suite, spec),
        "memory_material_summary": memory_summary,
        "offline_baseline_outcomes": baseline_outcomes,
    }
    # offline_baseline_outcomes are preserved with the plan, but are part of
    # its integrity digest and checked against the same four frozen graders.
    body = dict(plan)
    plan["plan_sha256"] = sha256_json(body)
    output_root = Path(output).expanduser().absolute()
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError("plan output must be a new directory")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(mode=0o700)
    _write_json(output_root / "comparison-plan.json", plan)
    _write_json(output_root / "comparison-spec.json", spec.value, limit=MAX_COMPARISON_SPEC_BYTES)
    return output_root


def _verify_plan(raw: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "plan_id", "comparison_id", "spec", "spec_sha256",
        "suite_summary", "source_snapshots", "adapter_sha256", "environment",
        "model_binding_ref", "execution_order", "memory_material_summary",
        "offline_baseline_outcomes", "plan_sha256",
    }
    if set(raw) != expected or raw.get("schema_version") != 1:
        raise ValueError("comparison-plan contract has unknown or missing fields")
    spec = validate_comparison_spec(raw["spec"])
    if raw["comparison_id"] != spec.comparison_id or raw["spec_sha256"] != spec.digest:
        raise ValueError("comparison plan spec digest mismatch")
    binding = raw["model_binding_ref"]
    if binding is not None:
        if not isinstance(binding, dict) or set(binding) != {"profile", "provider", "protocol", "fingerprint"}:
            raise ValueError("comparison plan model binding summary is invalid")
        if (binding["profile"] != spec.value["groups"][0]["model_profile"]
                or not isinstance(binding["provider"], str)
                or binding["protocol"] not in {"openai_chat", "anthropic_messages"}
                or not isinstance(binding["fingerprint"], str)
                or re.fullmatch(r"[0-9a-f]{64}", binding["fingerprint"]) is None):
            raise ValueError("comparison plan model binding summary is invalid")
    if (not isinstance(raw["source_snapshots"], dict)
            or set(raw["source_snapshots"]) != {group["group_id"] for group in spec.value["groups"]}):
        raise ValueError("comparison plan source snapshot groups are incomplete")
    for group in spec.value["groups"]:
        snapshot = raw["source_snapshots"][group["group_id"]]
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"revision", "package_version", "runtime_fingerprint"}
                or snapshot["revision"] != group["source_revision"]
                or snapshot["package_version"] != ("0.52.0" if group["group_id"] == "old-off" else "0.53.0")
                or not isinstance(snapshot["runtime_fingerprint"], str)
                or re.fullmatch(r"[0-9a-f]{64}", snapshot["runtime_fingerprint"]) is None):
            raise ValueError("comparison plan source snapshot is invalid")
    body = dict(raw)
    claimed = body.pop("plan_sha256")
    if claimed != sha256_json(body):
        raise ValueError("comparison plan digest mismatch")
    if not isinstance(raw["plan_id"], str) or re.fullmatch(r"[a-f0-9-]{36}", raw["plan_id"]) is None:
        raise ValueError("comparison plan ID invalid")
    return raw


def _case_by_id(suite, case_id: str):
    try:
        return next(item for item in suite.cases if item.case.case_id == case_id)
    except StopIteration as error:
        raise ValueError("comparison slot references an unknown case") from error


def _make_grader_reference(suite_case, temp_root: Path) -> Path:
    reference = temp_root / "grader-reference"
    initial = reference / "initial"
    _copy_fixture(Path(suite_case.case.case_dir) / suite_case.case.fixture_dir, initial)
    for current, dirs, files in os.walk(reference, topdown=False):
        current_path = Path(current)
        for name in files:
            os.chmod(current_path / name, 0o400)
        os.chmod(current_path, 0o500)
    return reference


def _run_grader(suite_case, workspace: Path, reference: Path, home: Path,
                redactions: tuple[str, ...]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    case = suite_case.case
    grader_path = Path(case.case_dir) / case.grader_script
    if grader_path.is_symlink() or not grader_path.is_file():
        return {"passed": None, "error_kind": "grader_invalid", "duration_ms": None, "exit_code": None, "result": None}, "", {"complete": True, "issue": None}
    raw = grader_path.read_bytes()
    if len(raw) > 64 * 1024 or hashlib.sha256(raw).hexdigest() != suite_case.grader_sha256:
        return {"passed": None, "error_kind": "grader_changed", "duration_ms": None, "exit_code": None, "result": None}, "", {"complete": True, "issue": None}
    return run_frozen_grader_source(
        case, raw, suite_case.grader_sha256, workspace, reference, home, redactions,
        include_cleanup=True,
    )


def _invoke_trial(
    *, run_id: str, slot: dict[str, Any], spec: ComparisonSpec, suite_case,
    group: dict[str, Any], source_root: Path, source_fingerprint: str,
    seeds: dict[str, dict[str, Any]], output_root: Path,
    fixture_actions: list[dict[str, Any]] | None, run_kind: str,
    binding_payload: dict[str, Any] | None, redactions: tuple[str, ...],
) -> tuple[dict[str, Any], str, str]:
    trial_id = str(uuid.uuid4())
    temp_root = create_private_temp_dir("mini-agent-comparison-trial-")
    os.chmod(temp_root, 0o700)
    workspace = temp_root / "workspace"
    home = temp_root / "home"
    home.mkdir(mode=0o700)
    (home / "tmp").mkdir(mode=0o700)
    memory_dir = temp_root / "memory"
    memory_dir.mkdir(mode=0o700)
    result_payload: dict[str, Any] | None = None
    worker_info: dict[str, Any] | None = None
    grader: dict[str, Any] = {"passed": None, "error_kind": "agent_not_started", "duration_ms": None, "exit_code": None, "result": None}
    grader_log = ""
    agent_log = ""
    diff_text = ""
    changed_files: list[str] = []
    workspace_error: str | None = None
    cleanup_complete = True
    cleanup_issue = None
    preserve_temp_root = False
    began = False
    agent_marker_started = False
    try:
        case = suite_case.case
        _copy_fixture(Path(case.case_dir) / case.fixture_dir, workspace)
        before, _file_count, _total_bytes = _snapshot(workspace)
        reference = _make_grader_reference(suite_case, temp_root)
        request = {
            "schema_version": 1,
            "trial_id": trial_id,
            "case": case.to_dict(),
            "workspace": str(workspace),
            "run_kind": run_kind,
            "responses": _wire_responses(fixture_actions or ()),
            "memory_retrieval_enabled": group["memory_retrieval_enabled"],
            "memory_dir": str(memory_dir),
            "memory_seeds": list(seeds.values()),
            "memory_material_sha256": group["memory_material_sha256"],
            "model_profile": group["model_profile"],
            "max_total_tokens": spec.value["budget"]["max_total_tokens"],
            "started_marker": str(temp_root / "agent-started.marker"),
        }
        request_bytes = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(request_bytes) > MAX_COMPARISON_TRIAL_BYTES:
            raise ValueError("comparison_worker_request_too_large")
        request_path = temp_root / "worker-request.json"
        _write_bytes(request_path, request_bytes)
        stdout, stderr = temp_root / "worker.stdout", temp_root / "worker.stderr"
        env = _process_environment(home, include_proxy=(run_kind == "live"))
        env["PYTHONPATH"] = str(source_root / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if binding_payload is not None:
            env["MINI_AGENT_COMPARISON_BINDING_JSON"] = json.dumps(
                binding_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
        began = True
        worker_info = _run_child(
            [sys.executable, str(COMPARISON_WORKER), str(request_path)],
            cwd=workspace, env=env, timeout=case.agent_timeout_seconds,
            stdout_path=stdout, stderr_path=stderr,
        )
        worker_stopped = worker_info.get("cleanup_complete", True)
        if not worker_stopped:
            cleanup_complete = False
            cleanup_issue = (
                f"worker_cleanup_incomplete:pid={worker_info.get('pid')}:"
                f"{worker_info.get('cleanup_issue') or 'unknown'}:temp_id={temp_root.name}"
            )
            preserve_temp_root = True
        if worker_stopped and not worker_info.get("timed_out") and not worker_info.get("interrupted"):
            try:
                text = _read_limited(stdout, MAX_WORKER_RESULT_BYTES)
                result_payload = json.loads(text) if text.strip() else None
                if not isinstance(result_payload, dict):
                    result_payload = None
            except (UnicodeError, json.JSONDecodeError):
                result_payload = None
        agent_log_payload = {
            "worker_returncode": worker_info.get("returncode"),
            "worker_timed_out": worker_info.get("timed_out"),
            "worker_stderr": _redact(_read_limited(stderr, MAX_AGENT_LOG_BYTES), redactions) if worker_stopped else "",
        }
        agent_log = json.dumps(agent_log_payload, ensure_ascii=False, sort_keys=True)[:MAX_AGENT_LOG_BYTES]
        if worker_stopped:
            try:
                after, _after_count, _after_bytes = _snapshot(workspace)
                diff_text, changed_files = _build_diff(before, after)
                if len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
                    workspace_error = "diff_too_large"
            except Exception as error:
                workspace_error = type(error).__name__
        else:
            workspace_error = "worker_cleanup_incomplete"
        agent_marker_started = (temp_root / "agent-started.marker").is_file()
        grader_cleanup = {"complete": True, "issue": None}
        if agent_marker_started and worker_stopped and not worker_info.get("interrupted"):
            grader, grader_log, grader_cleanup = _run_grader(suite_case, workspace, reference, home, redactions)
            if not grader_cleanup["complete"]:
                cleanup_complete = False
                cleanup_issue = (
                    f"grader_cleanup_incomplete:{grader_cleanup.get('issue') or 'unknown'}:"
                    f"temp_id={temp_root.name}"
                )
                preserve_temp_root = True
    finally:
        if not preserve_temp_root:
            try:
                # Restore only the runner-created grader reference permissions.
                for current, dirs, files in os.walk(temp_root, topdown=False):
                    current_path = Path(current)
                    for name in files:
                        path = current_path / name
                        try:
                            os.chmod(path, 0o600)
                        except OSError:
                            pass
                    for name in dirs:
                        path = current_path / name
                        try:
                            os.chmod(path, 0o700)
                        except OSError:
                            pass
                shutil.rmtree(temp_root)
            except OSError as error:
                cleanup_complete = False
                cleanup_issue = f"comparison_temp_cleanup:{type(error).__name__}"

    if worker_info is not None and worker_info.get("timed_out"):
        stop_reason, agent_error = "timeout", None
    elif worker_info is not None and worker_info.get("interrupted"):
        stop_reason, agent_error = "interrupted", "interrupted"
    elif result_payload is None:
        stop_reason, agent_error = "worker_error", "worker_protocol_error" if began else "worker_not_started"
    else:
        stop_reason = result_payload.get("stop_reason")
        agent_error = result_payload.get("error_kind")
    memory_result = result_payload.get("memory_evidence") if result_payload else None
    memory_evidence = {
        "retrieval_occurred": bool(memory_result.get("retrieval_occurred")) if isinstance(memory_result, dict) else False,
        "retrieval_attempts": int(memory_result.get("retrieval_attempts", 0)) if isinstance(memory_result, dict) else 0,
        "failure_categories": memory_result.get("failure_categories", []) if isinstance(memory_result, dict) else [],
        "material_sha256": group["memory_material_sha256"],
    }
    if result_payload is not None and memory_result and memory_result.get("material_sha256") != group["memory_material_sha256"]:
        agent_error = "memory_material_mismatch"
    agent = {
        "started": bool(result_payload.get("agent_started")) if result_payload else agent_marker_started,
        "stop_reason": stop_reason,
        "state_status": result_payload.get("state_status") if result_payload else None,
        "error_kind": agent_error,
        "llm_calls": result_payload.get("llm_calls") if result_payload else None,
        "successful_responses": result_payload.get("successful_responses") if result_payload else None,
        "tool_calls": result_payload.get("tool_calls") if result_payload else None,
        "tool_outcomes": result_payload.get("tool_outcomes") if result_payload else {"succeeded": 0, "failed": 0, "denied": 0, "invalid": 0, "timeout": 0},
        "permission_denials": result_payload.get("permission_denials") if result_payload else None,
        "input_tokens": result_payload.get("input_tokens") if result_payload else None,
        "output_tokens": result_payload.get("output_tokens") if result_payload else None,
        "token_source": result_payload.get("token_source", "unavailable") if result_payload else "unavailable",
        "duration_ms": (
            int(result_payload.get("duration_ms", 0))
            if result_payload and result_payload.get("agent_started") is True
            else int(worker_info["duration_ms"])
            if worker_info and worker_info.get("started") and agent_marker_started
            else None
        ),
        "invalid_repeat_count": result_payload.get("invalid_repeat_count") if result_payload else None,
        "subagent_calls": result_payload.get("subagent_calls", 0) if result_payload else 0,
    }
    if (worker_info is not None and worker_info.get("timed_out")):
        agent.update({"llm_calls": None, "successful_responses": None, "tool_calls": None, "permission_denials": None, "input_tokens": None, "output_tokens": None, "invalid_repeat_count": None})
    if workspace_error:
        agent["error_kind"] = workspace_error
    if not cleanup_complete:
        agent["error_kind"] = "cleanup_incomplete"
    if grader["error_kind"] == "agent_not_started" and agent_marker_started:
        grader["error_kind"] = "grader_not_run"
    binding_ref = result_payload.get("model_binding_ref") if result_payload else None
    if binding_ref is None and run_kind == "fixture":
        binding_ref = None
    trial: dict[str, Any] = {
        "schema_version": 1,
        "trial_id": trial_id,
        "run_id": run_id,
        "slot_id": slot["slot_id"],
        "group_id": group["group_id"],
        "case_id": suite_case.case.case_id,
        "repetition": slot["repetition"],
        "source_revision": group["source_revision"],
        "source_fingerprint": source_fingerprint,
        "suite_sha256": spec.value["suite"]["sha256"],
        "model_binding_ref": binding_ref,
        "memory_retrieval_enabled": group["memory_retrieval_enabled"],
        "memory_material_sha256": group["memory_material_sha256"],
        "memory_evidence": memory_evidence,
        "agent": agent,
        "grader": grader,
        "cleanup": {"complete": cleanup_complete, "issue": cleanup_issue},
        "evidence": {"artifacts": {}},
    }
    trial = validate_comparison_trial(trial).value
    artifact_payloads = {
        "diff": _redact(diff_text, redactions).encode("utf-8"),
        "agent_log": _redact(agent_log, redactions).encode("utf-8"),
        "grader_log": _redact(grader_log, redactions).encode("utf-8"),
    }
    for name, payload in artifact_payloads.items():
        if len(payload) > 1024 * 1024:
            raise ValueError(f"comparison {name} evidence exceeds its limit")
    trial_dir = output_root / "trials" / slot["slot_id"]
    trial_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=".comparison-trial-", dir=trial_dir.parent))
    os.chmod(stage, 0o700)
    try:
        artifacts = {}
        for name, payload in artifact_payloads.items():
            filename = {"diff": "diff.patch", "agent_log": "agent.log", "grader_log": "grader.log"}[name]
            _atomic_write(stage / filename, payload)
            artifacts[name] = {
                "path": f"trials/{slot['slot_id']}/{filename}",
                "sha256": sha256_bytes(payload), "size_bytes": len(payload),
            }
        trial["evidence"]["artifacts"] = artifacts
        trial = validate_comparison_trial(trial).value
        trial_bytes = json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        if len(trial_bytes) > MAX_COMPARISON_TRIAL_BYTES:
            raise ValueError("comparison trial output exceeds its limit")
        _atomic_write(stage / "trial.json", trial_bytes)
        if trial_dir.exists() or trial_dir.is_symlink():
            raise FileExistsError("comparison trial slot already has evidence; automatic retry is forbidden")
        os.replace(stage, trial_dir)
        directory_fd = os.open(trial_dir.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    trial_relative = (Path("trials") / slot["slot_id"] / "trial.json").as_posix()
    return trial, trial_relative, sha256_bytes(trial_bytes)


def run_comparison(plan_path: str | os.PathLike[str], output: str | os.PathLike[str], *, live: bool) -> Path:
    if not live:
        raise ValueError("comparison Runtime execution requires the explicit --live flag")
    plan_file = Path(plan_path).expanduser().absolute()
    raw_plan = _verify_plan(_read_json(plan_file, MAX_COMPARISON_RUN_BYTES))
    spec = validate_comparison_spec(raw_plan["spec"])
    suite = _load_suite(spec)
    _validate_v053_spec(spec, suite)
    seeds, memory_summary = _load_memory_materials(spec, suite)
    if memory_summary != raw_plan["memory_material_summary"]:
        raise ValueError("frozen Memory material changed after plan creation")
    require_clean_worktree()
    if current_commit() != spec.value["groups"][1]["source_revision"]:
        raise ValueError("current source revision changed after plan review")
    if adapter_fingerprint() != raw_plan["adapter_sha256"]:
        raise ValueError("comparison worker adapter changed after plan review")
    if environment_snapshot() != raw_plan["environment"]:
        raise ValueError("Python/platform source changed after plan review")
    binding_payload, binding_ref, redactions = _load_model_binding(spec.value["groups"][0]["model_profile"])
    if binding_ref != raw_plan["model_binding_ref"]:
        raise ValueError("local model binding changed after plan review")
    if raw_plan["suite_summary"] != _suite_summary(suite):
        raise ValueError("suite tasks, graders, permissions, or budget inputs changed after plan review")
    if raw_plan["execution_order"] != _execution_order(suite, spec):
        raise ValueError("comparison execution order differs from the deterministic frozen schedule")
    baseline_outcomes = benchmark.validate_suite_baselines(suite)
    if baseline_outcomes != raw_plan["offline_baseline_outcomes"]:
        raise ValueError("grader calibration changed after plan review")

    checkout_stack = ExitStack()
    checkouts: dict[str, Path] = {}
    source_by_revision: dict[str, dict[str, Any]] = {}
    try:
        for group in spec.value["groups"]:
            revision = group["source_revision"]
            if revision in checkouts:
                continue
            checkout = checkout_stack.enter_context(source_checkout(revision))
            check = preflight_source(checkout)
            expected_source = raw_plan["source_snapshots"][group["group_id"]]
            if check["runtime_fingerprint"] != expected_source["runtime_fingerprint"]:
                raise ValueError("target runtime source changed after plan review")
            target_suite = benchmark.load_suite(checkout / "tests/fixtures/evaluation/benchmark/suite.json", require_pinned=True)
            if target_suite.suite_sha256 != suite.suite_sha256:
                raise ValueError("target checkout suite changed after plan review")
            checkouts[revision] = checkout
            source_by_revision[revision] = expected_source

        output_root = Path(output).expanduser().absolute()
        if output_root.exists() or output_root.is_symlink():
            raise FileExistsError("run output must be a brand new directory")
        output_root.parent.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(mode=0o700)
        _write_json(output_root / "comparison-plan.json", raw_plan)
        _write_json(output_root / "comparison-spec.json", spec.value, limit=MAX_COMPARISON_SPEC_BYTES)
        suite_copy = output_root / "suite.json"
        _write_bytes(suite_copy, suite.suite_bytes)
        execution_order = raw_plan["execution_order"]
        slots = [
            {**entry, "status": "not_run", "trial_path": None, "trial_sha256": None, "error_kind": None}
            for entry in execution_order
        ]
        run_id = str(uuid.uuid4())
        run: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "comparison_id": spec.comparison_id,
            "spec_sha256": spec.digest,
            "plan_sha256": raw_plan["plan_sha256"],
            "suite": raw_plan["suite_summary"],
            "frozen_sources": raw_plan["source_snapshots"],
            "environment": raw_plan["environment"],
            "model_binding_ref": binding_ref,
            "run_kind": "live",
            "execution_order": execution_order,
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "slots": slots,
        }
        ledger_path = output_root / "comparison-run.json"
        _write_json(ledger_path, validate_comparison_run(run).value)
        group_by_id = {group["group_id"]: group for group in spec.value["groups"]}
        try:
            for index, slot in enumerate(run["slots"]):
                group = group_by_id[slot["group_id"]]
                suite_case = _case_by_id(suite, slot["case_id"])
                revision = group["source_revision"]
                checkout = checkouts[revision]
                if source_tree_fingerprint(checkout / "src") != raw_plan["source_snapshots"][group["group_id"]]["runtime_fingerprint"]:
                    slot["status"] = "infrastructure_error"
                    slot["error_kind"] = "source_changed_before_trial"
                    run["updated_at"] = _now()
                    _write_json(ledger_path, validate_comparison_run(run).value)
                    continue
                slot["status"] = "running"
                run["updated_at"] = _now()
                _write_json(ledger_path, validate_comparison_run(run).value)
                try:
                    result, relative, digest = _invoke_trial(
                        run_id=run_id, slot=slot, spec=spec, suite_case=suite_case,
                        group=group, source_root=checkout,
                        source_fingerprint=raw_plan["source_snapshots"][group["group_id"]]["runtime_fingerprint"],
                        seeds=seeds, output_root=output_root, fixture_actions=None,
                        run_kind="live", binding_payload=binding_payload,
                        redactions=redactions,
                    )
                    if source_tree_fingerprint(checkout / "src") != raw_plan["source_snapshots"][group["group_id"]]["runtime_fingerprint"]:
                        raise RuntimeError("source_changed_after_trial")
                    if result["agent"]["stop_reason"] == "interrupted":
                        slot["status"] = "interrupted"
                        slot["trial_path"] = relative
                        slot["trial_sha256"] = digest
                        slot["error_kind"] = "interrupted"
                        run["status"] = "interrupted"
                        for rest in run["slots"][index + 1:]:
                            if rest["status"] == "running":
                                rest["status"] = "not_run"
                        run["updated_at"] = _now()
                        _write_json(ledger_path, validate_comparison_run(run).value)
                        return ledger_path
                    slot["status"] = "completed"
                    slot["trial_path"] = relative
                    slot["trial_sha256"] = digest
                    slot["error_kind"] = None
                except (KeyboardInterrupt, SystemExit):
                    slot["status"] = "interrupted"
                    slot["error_kind"] = "interrupted"
                    for rest in run["slots"][index + 1:]:
                        if rest["status"] == "running":
                            rest["status"] = "not_run"
                    run["status"] = "interrupted"
                    run["updated_at"] = _now()
                    _write_json(ledger_path, validate_comparison_run(run).value)
                    raise
                except Exception as error:
                    slot["status"] = "infrastructure_error"
                    slot["trial_path"] = None
                    slot["trial_sha256"] = None
                    slot["error_kind"] = type(error).__name__
                run["updated_at"] = _now()
                if index == len(run["slots"]) - 1:
                    run["status"] = "completed"
                _write_json(ledger_path, validate_comparison_run(run).value)
        except (KeyboardInterrupt, SystemExit):
            raise
        return ledger_path
    finally:
        checkout_stack.close()


def _fixture_spec() -> ComparisonSpec:
    suite = benchmark.load_suite(REPOSITORY_ROOT / "tests/fixtures/evaluation/benchmark/suite.json")
    material_rows: list[dict[str, str]] = []
    for item in suite.cases:
        relative = f"tests/fixtures/evaluation/comparison/memories/{item.case.case_id}.json"
        raw = json.loads(_read_regular_below(REPOSITORY_ROOT, relative, 16 * 1024).decode("utf-8"))
        semantic = _semantic_memory(raw)
        material_rows.append({
            "case_id": item.case.case_id,
            "path": relative,
            "semantic_sha256": sha256_json(semantic),
        })
    combined = sha256_json([
        {"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]}
        for item in material_rows
    ])
    current = current_commit()
    raw_spec = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "comparison_id": "coding-benchmark-v053-self-test",
        "version": "1.0",
        "suite": {
            "suite_id": suite.suite_id, "version": suite.version,
            "sha256": suite.suite_sha256,
            "path": "tests/fixtures/evaluation/benchmark/suite.json",
        },
        "groups": [
            {"group_id": "old-off", "source_revision": PINNED_V052_REVISION,
             "model_profile": "offline-fixture", "memory_retrieval_enabled": False,
             "memory_material_sha256": combined},
            {"group_id": "current-off", "source_revision": current,
             "model_profile": "offline-fixture", "memory_retrieval_enabled": False,
             "memory_material_sha256": combined},
            {"group_id": "current-on", "source_revision": current,
             "model_profile": "offline-fixture", "memory_retrieval_enabled": True,
             "memory_material_sha256": combined},
        ],
        "edges": [
            {"baseline_group": "old-off", "experiment_group": "current-off", "allowed_changes": ["source_revision"]},
            {"baseline_group": "current-off", "experiment_group": "current-on", "allowed_changes": ["memory_retrieval_enabled"]},
        ],
        "repeats": 3,
        "budget": {"max_tool_calls": 48, "max_total_tokens": 64000},
        "memory_materials": material_rows,
        "price_snapshot": {
            "currency": "USD", "source": "fixture-only: no provider request",
            "effective_date": "not-applicable",
            "input_usd_per_million": None, "output_usd_per_million": None,
        },
        "metrics_rules_version": "1",
    }
    return validate_comparison_spec(raw_spec)


def self_test_comparison(output: str | os.PathLike[str]) -> dict[str, Any]:
    """Run the frozen 36-slot fixture matrix plus temporary Runtime boundary probes."""
    spec = _fixture_spec()
    suite = _load_suite(spec)
    _validate_v053_spec(spec, suite)
    require_clean_worktree()
    current = current_commit()
    if current != spec.value["groups"][1]["source_revision"]:
        raise ValueError("self-test spec current revision must equal clean HEAD")
    case_fixture = load_fixture_responses()
    for item in suite.cases:
        if item.case.case_id not in case_fixture:
            raise ValueError("responses.json is missing a coding suite case")
        if set(case_fixture[item.case.case_id]) != {"success", "failure", "denied", "repeated"}:
            raise ValueError("responses.json must cover success, failure, denied, and repeated scenarios")
    plan_parent = create_private_temp_dir("mini-agent-comparison-self-test-plan-")
    try:
        spec_path = plan_parent / "comparison-self-test-spec.json"
        _write_json(spec_path, spec.value, limit=MAX_COMPARISON_SPEC_BYTES)
        plan_dir = plan_parent / "plan"
        plan_comparison(spec_path, plan_dir, require_binding=False)
        plan = _verify_plan(_read_json(plan_dir / "comparison-plan.json", MAX_COMPARISON_RUN_BYTES))
        seeds, _summary = _load_memory_materials(spec, suite)
        group_by_id = {group["group_id"]: group for group in spec.value["groups"]}
        responses = case_fixture
        plan_path = plan_dir / "comparison-plan.json"
        run_path = run_comparison_fixture(plan, suite, spec, output, seeds, responses)
        probes: dict[str, Any] = {}
        # The probes use a disposable workspace and never become comparison slots.
        with source_checkout(current) as checkout:
            first_case = suite.cases[0]
            probe_group = dict(group_by_id["current-off"])
            for name in ("failure", "denied", "repeated"):
                if name == "denied":
                    narrowed_case = replace(first_case.case, authorized_tools=())
                    probe_suite_case = replace(first_case, case=narrowed_case)
                else:
                    probe_suite_case = first_case
                slot = {"slot_id": f"probe-{name}", "group_id": "current-off", "case_id": first_case.case.case_id, "repetition": 1}
                # A temporary artifact root is removed after metrics are checked.
                probe_root = create_private_temp_dir("mini-agent-comparison-probe-")
                try:
                    result, _relative, _digest = _invoke_trial(
                        run_id=str(uuid.uuid4()), slot=slot, spec=spec,
                        suite_case=probe_suite_case,
                        group=probe_group, source_root=checkout,
                        source_fingerprint=source_tree_fingerprint(checkout / "src"),
                        seeds=seeds, output_root=probe_root,
                        fixture_actions=responses[first_case.case.case_id][name],
                        run_kind="fixture", binding_payload=None, redactions=(),
                    )
                    probes[name] = {
                        "tool_outcomes": result["agent"]["tool_outcomes"],
                        "permission_denials": result["agent"]["permission_denials"],
                        "invalid_repeat_count": result["agent"]["invalid_repeat_count"],
                    }
                finally:
                    shutil.rmtree(probe_root, ignore_errors=True)
        if probes["failure"]["tool_outcomes"]["failed"] < 1:
            raise RuntimeError("comparison failure probe did not exercise a failed tool result")
        if probes["denied"]["tool_outcomes"]["denied"] < 1 or probes["denied"]["permission_denials"] < 1:
            raise RuntimeError("comparison denial probe did not exercise PermissionGate")
        if probes["repeated"]["invalid_repeat_count"] < 1:
            raise RuntimeError("comparison repeated-call probe was not detected")
        from mini_agent.evaluation.comparison_report import build_comparison_report
        report = build_comparison_report(run_path.parent)
        if not report["batch_complete"]:
            raise RuntimeError("comparison fixture matrix did not complete all 36 slots")
        for group in report["groups"].values():
            if group["independent_acceptance"]["passed"] != 12 or group["independent_acceptance"]["denominator"] != 12:
                raise RuntimeError("comparison fixture Runtime did not pass every independent grader")
        summary = {"self_test": "passed", "run_dir": str(run_path.parent), "probe_metrics": probes, "planned_trials": 36}
        _write_json(run_path.parent / "self-test.json", summary)
        return summary
    finally:
        shutil.rmtree(plan_parent, ignore_errors=True)


def run_comparison_fixture(plan: dict[str, Any], suite, spec: ComparisonSpec,
                           output: str | os.PathLike[str], seeds: dict[str, dict[str, Any]],
                           response_fixtures: dict[str, Any]) -> Path:
    """Fixture-only counterpart used by the self-test; it cannot contact a provider."""
    from contextlib import ExitStack

    binding_ref = plan["model_binding_ref"]
    _ = binding_ref
    output_root = Path(output).expanduser().absolute()
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError("self-test output must be a brand new directory")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(mode=0o700)
    _write_json(output_root / "comparison-plan.json", plan)
    _write_json(output_root / "comparison-spec.json", spec.value, limit=MAX_COMPARISON_SPEC_BYTES)
    _write_bytes(output_root / "suite.json", suite.suite_bytes)
    checkouts: dict[str, Path] = {}
    with ExitStack() as stack:
        for group in spec.value["groups"]:
            revision = group["source_revision"]
            if revision not in checkouts:
                checkout = stack.enter_context(source_checkout(revision))
                preflight = preflight_source(checkout)
                if preflight["runtime_fingerprint"] != plan["source_snapshots"][group["group_id"]]["runtime_fingerprint"]:
                    raise ValueError("fixture target source differs from review plan")
                checkouts[revision] = checkout
        execution_order = plan["execution_order"]
        slots = [{**item, "status": "not_run", "trial_path": None, "trial_sha256": None, "error_kind": None} for item in execution_order]
        run_id = str(uuid.uuid4())
        run = {
            "schema_version": 1, "run_id": run_id, "comparison_id": spec.comparison_id,
            "spec_sha256": spec.digest, "plan_sha256": plan["plan_sha256"],
            "suite": plan["suite_summary"], "frozen_sources": plan["source_snapshots"],
            "environment": plan["environment"], "model_binding_ref": plan["model_binding_ref"],
            "run_kind": "fixture",
            "execution_order": execution_order, "status": "running", "started_at": _now(),
            "updated_at": _now(), "slots": slots,
        }
        ledger_path = output_root / "comparison-run.json"
        _write_json(ledger_path, validate_comparison_run(run).value)
        groups = {item["group_id"]: item for item in spec.value["groups"]}
        try:
            for index, slot in enumerate(run["slots"]):
                group = groups[slot["group_id"]]
                suite_case = _case_by_id(suite, slot["case_id"])
                slot["status"] = "running"
                run["updated_at"] = _now()
                _write_json(ledger_path, validate_comparison_run(run).value)
                try:
                    result, relative, digest = _invoke_trial(
                        run_id=run_id, slot=slot, spec=spec, suite_case=suite_case,
                        group=group, source_root=checkouts[group["source_revision"]],
                        source_fingerprint=plan["source_snapshots"][group["group_id"]]["runtime_fingerprint"],
                        seeds=seeds, output_root=output_root,
                        fixture_actions=response_fixtures[slot["case_id"]]["success"],
                        run_kind="fixture", binding_payload=None, redactions=(),
                    )
                    slot["status"] = "completed"
                    slot["trial_path"] = relative
                    slot["trial_sha256"] = digest
                    slot["error_kind"] = None
                except Exception as error:
                    slot["status"] = "infrastructure_error"
                    slot["error_kind"] = type(error).__name__
                run["updated_at"] = _now()
                if index == len(run["slots"]) - 1:
                    run["status"] = "completed"
                _write_json(ledger_path, validate_comparison_run(run).value)
        except (KeyboardInterrupt, SystemExit):
            for slot in run["slots"]:
                if slot["status"] == "running":
                    slot["status"] = "interrupted"
                    slot["error_kind"] = "interrupted"
            run["status"] = "interrupted"
            run["updated_at"] = _now()
            _write_json(ledger_path, validate_comparison_run(run).value)
            raise
    return ledger_path


__all__ = [
    "validate_comparison", "plan_comparison", "run_comparison", "self_test_comparison",
    "load_fixture_responses", "run_comparison_fixture",
]
