"""Strict, independent contracts for frozen regression comparisons.

These contracts intentionally do not extend the historical Evaluation Harness
or Reliability schemas.  They contain source references and bounded evidence
metadata, never provider credentials or prompt bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any


COMPARISON_SCHEMA_VERSION = 1
MAX_COMPARISON_GROUPS = 8
MAX_COMPARISON_REPEATS = 10
MAX_COMPARISON_SLOTS = 320
MAX_COMPARISON_SPEC_BYTES = 256 * 1024
MAX_COMPARISON_RUN_BYTES = 2 * 1024 * 1024
MAX_COMPARISON_TRIAL_BYTES = 256 * 1024
PINNED_V052_REVISION = "9d498f91f7ca21caa7f4f842fba0a49075754f20"

_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
_RATE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?\Z")


COMPARISON_SPEC_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "mini-agent-comparison-spec-v1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "comparison_id", "version", "suite", "groups",
        "edges", "repeats", "budget", "memory_materials", "price_snapshot",
        "metrics_rules_version",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "comparison_id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"},
        "version": {"type": "string", "maxLength": 64},
        "suite": {"type": "object"},
        "groups": {"type": "array", "minItems": 2, "maxItems": 8},
        "edges": {"type": "array", "minItems": 1, "maxItems": 7},
        "repeats": {"type": "integer", "minimum": 1, "maximum": 10},
        "budget": {"type": "object"},
        "memory_materials": {"type": "array", "minItems": 1, "maxItems": 16},
        "price_snapshot": {"type": "object"},
        "metrics_rules_version": {"type": "string", "maxLength": 64},
    },
}


@dataclass(frozen=True)
class ComparisonSpec:
    """Validated comparison inputs, with all semantically relevant fields frozen."""

    value: dict[str, Any]

    @property
    def comparison_id(self) -> str:
        return self.value["comparison_id"]

    @property
    def digest(self) -> str:
        return sha256_json(self.value)


@dataclass(frozen=True)
class ComparisonRun:
    """Append-updated batch ledger with one terminal status per planned slot."""

    value: dict[str, Any]


@dataclass(frozen=True)
class ComparisonTrial:
    """One group/case/repetition result and links to its raw evidence."""

    value: dict[str, Any]


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def comparison_execution_order(case_ids: list[str], groups: list[dict[str, Any]], repeats: int) -> list[dict[str, Any]]:
    """Create the frozen repetition/case order with rotating group positions."""
    rows: list[dict[str, Any]] = []
    order = 0
    for repetition in range(1, repeats + 1):
        for case_index, case_id in enumerate(case_ids):
            shift = (case_index + repetition - 1) % len(groups)
            rotated = groups[shift:] + groups[:shift]
            for group in rotated:
                seed = f"{group['group_id']}\0{case_id}\0{repetition}".encode()
                slot_id = "s-" + hashlib.sha256(seed).hexdigest()[:24]
                rows.append({
                    "slot_id": slot_id, "group_id": group["group_id"],
                    "case_id": case_id, "repetition": repetition, "order": order,
                })
                order += 1
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _object(value: Any, required: set[str], where: str, optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{where} 必须是 JSON object")
    _require(required <= set(value) <= required | optional, f"{where} 字段不完整或包含未知字段")
    return value


def _safe_relpath(value: Any, where: str) -> str:
    _require(isinstance(value, str) and 0 < len(value) <= 512, f"{where} 路径无效")
    _require("\\" not in value and "\x00" not in value, f"{where} 路径无效")
    parts = value.split("/")
    _require(not value.startswith("/") and all(p not in {"", ".", ".."} for p in parts), f"{where} 必须是安全相对路径")
    return value


def _reject_sensitive_keys(value: Any, where: str = "comparison") -> None:
    forbidden = {
        "api_key", "apikey", "authorization", "endpoint", "base_url",
        "model_id", "extra_headers", "auth_headers", "secret", "password",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            _require(isinstance(key, str), f"{where} 字段名必须是字符串")
            _require(key.casefold() not in forbidden, f"{where} 含敏感配置字段: {key}")
            _reject_sensitive_keys(child, where)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child, where)


def validate_comparison_spec(raw: Any) -> ComparisonSpec:
    root = _object(raw, {
        "schema_version", "comparison_id", "version", "suite", "groups",
        "edges", "repeats", "budget", "memory_materials", "price_snapshot",
        "metrics_rules_version",
    }, "ComparisonSpec")
    _reject_sensitive_keys(root)
    _require(root["schema_version"] == COMPARISON_SCHEMA_VERSION, "ComparisonSpec schema_version 不支持")
    for name in ("comparison_id",):
        _require(isinstance(root[name], str) and _ID.fullmatch(root[name]) is not None, f"{name} 格式无效")
    _require(isinstance(root["version"], str) and 0 < len(root["version"]) <= 64, "version 无效")
    _require(isinstance(root["metrics_rules_version"], str) and 0 < len(root["metrics_rules_version"]) <= 64, "metrics_rules_version 无效")

    suite = _object(root["suite"], {"suite_id", "version", "sha256", "path"}, "suite")
    _require(isinstance(suite["suite_id"], str) and _ID.fullmatch(suite["suite_id"]) is not None, "suite_id 无效")
    _require(isinstance(suite["version"], str) and 0 < len(suite["version"]) <= 64, "suite.version 无效")
    _require(isinstance(suite["sha256"], str) and _SHA256.fullmatch(suite["sha256"]) is not None, "suite.sha256 无效")
    _safe_relpath(suite["path"], "suite.path")

    repeats = root["repeats"]
    _require(type(repeats) is int and 1 <= repeats <= MAX_COMPARISON_REPEATS, "repeats 必须是 1–10 的整数")
    budget = _object(root["budget"], {"max_tool_calls", "max_total_tokens"}, "budget")
    _require(type(budget["max_tool_calls"]) is int and budget["max_tool_calls"] == 48, "max_tool_calls 本比较固定为 48")
    _require(type(budget["max_total_tokens"]) is int and budget["max_total_tokens"] == 64000, "max_total_tokens 本比较固定为 64000")

    materials = root["memory_materials"]
    _require(isinstance(materials, list) and 1 <= len(materials) <= 16, "memory_materials 数量无效")
    material_by_case: dict[str, dict[str, Any]] = {}
    for index, raw_material in enumerate(materials):
        material = _object(raw_material, {"case_id", "path", "semantic_sha256"}, f"memory_materials[{index}]")
        _require(isinstance(material["case_id"], str) and _ID.fullmatch(material["case_id"]) is not None, "memory case_id 无效")
        _require(material["case_id"] not in material_by_case, "memory_materials 含重复 case_id")
        _safe_relpath(material["path"], "memory_material.path")
        _require(isinstance(material["semantic_sha256"], str) and _SHA256.fullmatch(material["semantic_sha256"]) is not None, "memory semantic_sha256 无效")
        material_by_case[material["case_id"]] = material

    groups = root["groups"]
    _require(isinstance(groups, list) and 2 <= len(groups) <= MAX_COMPARISON_GROUPS, "groups 数量必须为 2–8")
    _require(len(groups) * len(materials) * repeats <= MAX_COMPARISON_SLOTS, "ComparisonSpec 总槽位超过 320")
    group_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_group in enumerate(groups):
        group = _object(raw_group, {
            "group_id", "source_revision", "model_profile", "memory_retrieval_enabled",
            "memory_material_sha256",
        }, f"groups[{index}]")
        _require(isinstance(group["group_id"], str) and _ID.fullmatch(group["group_id"]) is not None, "group_id 无效")
        _require(group["group_id"] not in group_by_id, "group_id 重复")
        _require(isinstance(group["source_revision"], str) and _COMMIT.fullmatch(group["source_revision"]) is not None, "source_revision 必须是完整 commit ID")
        _require(isinstance(group["model_profile"], str) and _PROFILE.fullmatch(group["model_profile"]) is not None, "model_profile 必须是本地配置别名")
        _require(type(group["memory_retrieval_enabled"]) is bool, "memory_retrieval_enabled 必须是 bool")
        _require(isinstance(group["memory_material_sha256"], str) and _SHA256.fullmatch(group["memory_material_sha256"]) is not None, "memory_material_sha256 无效")
        group_by_id[group["group_id"]] = group
    _require(len({g["memory_material_sha256"] for g in groups}) == 1, "所有组必须共享同一语义记忆快照摘要")
    expected_materials_digest = sha256_json([
        {"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]}
        for item in materials
    ])
    _require(groups[0]["memory_material_sha256"] == expected_materials_digest, "group memory_material_sha256 与记忆材料摘要不一致")

    edges = root["edges"]
    _require(isinstance(edges, list) and 1 <= len(edges) <= MAX_COMPARISON_GROUPS - 1, "edges 数量无效")
    edge_pairs: set[tuple[str, str]] = set()
    # Model/provider comparisons are represented by distinct local aliases;
    # the v0.53 primary three-group contract still freezes one alias.
    allowed_factors = {"source_revision", "memory_retrieval_enabled", "model_profile"}
    for index, raw_edge in enumerate(edges):
        edge = _object(raw_edge, {"baseline_group", "experiment_group", "allowed_changes"}, f"edges[{index}]")
        baseline = edge["baseline_group"]
        experiment = edge["experiment_group"]
        _require(isinstance(baseline, str) and baseline in group_by_id, "edge baseline_group 未知")
        _require(isinstance(experiment, str) and experiment in group_by_id and experiment != baseline, "edge experiment_group 无效")
        pair = (baseline, experiment)
        _require(pair not in edge_pairs, "edges 含重复配对")
        edge_pairs.add(pair)
        changes = edge["allowed_changes"]
        _require(isinstance(changes, list) and 1 <= len(changes) <= 2 and len(set(changes)) == len(changes), "allowed_changes 无效")
        _require(set(changes) <= allowed_factors, "edge 声明了本版不支持的比较因素")
        left, right = group_by_id[baseline], group_by_id[experiment]
        differing = {key for key in left if key != "group_id" and left[key] != right[key]}
        _require(differing == set(changes), "比较边除预声明因素外还存在条件差异，或声明因素实际没有变化")

    price = _object(root["price_snapshot"], {
        "currency", "source", "effective_date", "input_usd_per_million", "output_usd_per_million",
    }, "price_snapshot")
    _require(price["currency"] == "USD", "price_snapshot.currency 必须为 USD")
    for field in ("source", "effective_date"):
        _require(isinstance(price[field], str) and 1 <= len(price[field]) <= 240, f"price_snapshot.{field} 无效")
    for field in ("input_usd_per_million", "output_usd_per_million"):
        value = price[field]
        if value is not None:
            _require(isinstance(value, str) and _RATE.fullmatch(value) is not None, f"price_snapshot.{field} 必须是定点十进制字符串或 null")
            try:
                _require(Decimal(value).is_finite() and Decimal(value) >= 0, f"price_snapshot.{field} 无效")
            except InvalidOperation as error:
                raise ValueError(f"price_snapshot.{field} 无效") from error

    normalized = json.loads(json.dumps(root, ensure_ascii=False, sort_keys=True))
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _require(len(encoded) <= MAX_COMPARISON_SPEC_BYTES, "ComparisonSpec 超过大小上限")
    return ComparisonSpec(normalized)


def validate_comparison_run(raw: Any) -> ComparisonRun:
    required = {
        "schema_version", "run_id", "comparison_id", "spec_sha256", "plan_sha256",
        "suite", "frozen_sources", "environment", "model_binding_ref", "execution_order",
        "run_kind", "status", "started_at", "updated_at", "slots",
    }
    run = _object(raw, required, "ComparisonRun")
    _reject_sensitive_keys(run, "ComparisonRun")
    _require(run["schema_version"] == 1, "ComparisonRun schema_version 不支持")
    for name in ("run_id",):
        _require(isinstance(run[name], str) and re.fullmatch(r"[a-f0-9-]{36}", run[name]) is not None, f"{name} 无效")
    for name in ("spec_sha256", "plan_sha256"):
        _require(isinstance(run[name], str) and _SHA256.fullmatch(run[name]) is not None, f"{name} 无效")
    _require(isinstance(run["comparison_id"], str) and _ID.fullmatch(run["comparison_id"]) is not None, "comparison_id 无效")
    _require(run["run_kind"] in {"live", "fixture"}, "ComparisonRun run_kind 无效")
    _require(run["status"] in {"running", "completed", "interrupted"}, "ComparisonRun status 无效")
    for field in ("started_at", "updated_at"):
        _require(isinstance(run[field], str) and len(run[field]) <= 64, f"{field} 无效")
    _require(isinstance(run["execution_order"], list) and len(run["execution_order"]) <= MAX_COMPARISON_SLOTS, "execution_order 无效")
    _require(isinstance(run["slots"], list) and len(run["slots"]) == len(run["execution_order"]), "slots 与 execution_order 数量不一致")
    seen: set[str] = set()
    seen_identity: set[tuple[str, str, int]] = set()
    statuses = {"not_run", "running", "completed", "infrastructure_error", "interrupted"}
    for index, slot in enumerate(run["slots"]):
        _object(slot, {"slot_id", "group_id", "case_id", "repetition", "order", "status", "trial_path", "trial_sha256", "error_kind"}, f"slots[{index}]")
        _require(isinstance(slot["slot_id"], str) and _ID.fullmatch(slot["slot_id"]) is not None, "slot_id 无效")
        _require(slot["slot_id"] not in seen, "ComparisonRun 含重复 slot_id")
        seen.add(slot["slot_id"])
        for field in ("group_id", "case_id"):
            _require(isinstance(slot[field], str) and _ID.fullmatch(slot[field]) is not None, f"slot.{field} 无效")
        _require(type(slot["repetition"]) is int and 1 <= slot["repetition"] <= MAX_COMPARISON_REPEATS, "slot.repetition 无效")
        identity = (slot["group_id"], slot["case_id"], slot["repetition"])
        _require(identity not in seen_identity, "ComparisonRun 含重复 group/case/repetition 槽位")
        seen_identity.add(identity)
        _require(type(slot["order"]) is int and slot["order"] == index, "slot.order 与数组顺序不一致")
        _require(slot["status"] in statuses, "slot.status 无效")
        if slot["trial_path"] is not None:
            _safe_relpath(slot["trial_path"], "slot.trial_path")
        _require(slot["status"] != "completed" or slot["trial_path"] is not None, "completed 槽位必须引用一条 trial")
        _require(slot["status"] in {"completed", "interrupted"} or slot["trial_path"] is None, "非终态槽位不能引用 trial")
        if slot["trial_path"] is not None:
            _require(isinstance(slot["trial_sha256"], str) and _SHA256.fullmatch(slot["trial_sha256"]) is not None, "completed 槽位必须绑定 trial 摘要")
        else:
            _require(slot["trial_sha256"] is None, "非 completed 槽位不能引用 trial 摘要")
        _require(slot["error_kind"] is None or (isinstance(slot["error_kind"], str) and len(slot["error_kind"]) <= 120), "slot.error_kind 无效")
        expected = run["execution_order"][index]
        _require({key: slot[key] for key in ("slot_id", "group_id", "case_id", "repetition", "order")} == expected, "slots 顺序与冻结 execution_order 不一致")
    slot_statuses = {slot["status"] for slot in run["slots"]}
    if run["status"] == "completed":
        _require(not slot_statuses.intersection({"running", "not_run", "interrupted"}), "completed run 含未收束槽位")
    elif run["status"] == "interrupted":
        _require("interrupted" in slot_statuses, "interrupted run 必须保留中断槽位")
    else:
        _require("interrupted" not in slot_statuses, "running run 不能含 interrupted 槽位")
    encoded = json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _require(len(encoded) <= MAX_COMPARISON_RUN_BYTES, "ComparisonRun 超过大小上限")
    return ComparisonRun(json.loads(json.dumps(run, ensure_ascii=False, sort_keys=True)))


def validate_error_diagnostic(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    diagnostic = _object(raw, {"category", "cause_type", "errno", "tls_verify_code"}, "error_diagnostic")
    pairs = {
        "unknown": "unknown", "tls_certificate": "SSLCertVerificationError",
        "dns": "gaierror", "connection_refused": "ConnectionRefusedError",
        "timeout": "TimeoutError", "tls": "SSLError", "connection_reset": "ConnectionResetError",
        "remote_disconnect": "RemoteDisconnected", "http_transport": "HTTPException", "os_error": "OSError",
    }
    _require(isinstance(diagnostic["category"], str) and diagnostic["category"] in pairs, "error_diagnostic category 无效")
    _require(diagnostic["cause_type"] == pairs[diagnostic["category"]], "error_diagnostic cause_type 无效")
    for field in ("errno", "tls_verify_code"):
        number = diagnostic[field]
        _require(number is None or type(number) is int and -(2 ** 31) <= number < 2 ** 31, "error_diagnostic code 无效")
    _require(diagnostic["tls_verify_code"] is None or diagnostic["category"] == "tls_certificate" and diagnostic["tls_verify_code"] >= 0, "error_diagnostic TLS code 无效")
    return dict(diagnostic)


def validate_comparison_trial(raw: Any) -> ComparisonTrial:
    required = {
        "schema_version", "trial_id", "run_id", "slot_id", "group_id", "case_id",
        "repetition", "source_revision", "source_fingerprint", "suite_sha256",
        "model_binding_ref", "memory_retrieval_enabled", "memory_material_sha256",
        "memory_evidence", "agent", "grader", "cleanup", "evidence",
    }
    trial = _object(raw, required, "ComparisonTrial")
    _reject_sensitive_keys(trial, "ComparisonTrial")
    _require(trial["schema_version"] == 1, "ComparisonTrial schema_version 不支持")
    for field in ("trial_id", "run_id"):
        _require(isinstance(trial[field], str) and re.fullmatch(r"[a-f0-9-]{36}", trial[field]) is not None, f"{field} 无效")
    for field in ("slot_id", "group_id", "case_id"):
        _require(isinstance(trial[field], str) and _ID.fullmatch(trial[field]) is not None, f"{field} 无效")
    _require(type(trial["repetition"]) is int and 1 <= trial["repetition"] <= MAX_COMPARISON_REPEATS, "repetition 无效")
    for field in ("source_revision",):
        _require(isinstance(trial[field], str) and _COMMIT.fullmatch(trial[field]) is not None, f"{field} 无效")
    for field in ("source_fingerprint", "suite_sha256", "memory_material_sha256"):
        _require(isinstance(trial[field], str) and _SHA256.fullmatch(trial[field]) is not None, f"{field} 无效")
    _require(type(trial["memory_retrieval_enabled"]) is bool, "memory_retrieval_enabled 无效")
    memory = _object(trial["memory_evidence"], {
        "retrieval_occurred", "retrieval_attempts", "failure_categories", "material_sha256",
    }, "memory_evidence")
    _require(type(memory["retrieval_occurred"]) is bool, "memory_evidence.retrieval_occurred 无效")
    _require(type(memory["retrieval_attempts"]) is int and memory["retrieval_attempts"] >= 0, "memory_evidence.retrieval_attempts 无效")
    _require(isinstance(memory["failure_categories"], list) and len(memory["failure_categories"]) <= 16, "memory_evidence.failure_categories 无效")
    _require(all(isinstance(item, str) and len(item) <= 120 for item in memory["failure_categories"]), "memory failure category 无效")
    _require(memory["material_sha256"] == trial["memory_material_sha256"], "memory evidence 摘要不一致")
    if not trial["memory_retrieval_enabled"]:
        _require(not memory["retrieval_occurred"] and memory["retrieval_attempts"] == 0, "关闭组出现了 Memory 检索")
    binding = trial["model_binding_ref"]
    if binding is not None:
        _object(binding, {"profile", "provider", "protocol", "fingerprint"}, "model_binding_ref")
        _require(isinstance(binding["profile"], str) and len(binding["profile"]) <= 120, "model binding profile 无效")
        _require(isinstance(binding["provider"], str) and len(binding["provider"]) <= 120, "model binding provider 无效")
        _require(binding["protocol"] in {"openai_chat", "anthropic_messages"}, "model binding protocol 无效")
        _require(isinstance(binding["fingerprint"], str) and _SHA256.fullmatch(binding["fingerprint"]) is not None, "model binding fingerprint 无效")

    agent = _object(trial["agent"], {
        "started", "stop_reason", "state_status", "error_kind", "llm_calls",
        "successful_responses", "tool_calls", "tool_outcomes", "permission_denials",
        "input_tokens", "output_tokens", "token_source", "duration_ms",
        "invalid_repeat_count", "subagent_calls",
    }, "agent", frozenset({"error_diagnostic"}))
    if "error_diagnostic" in agent:
        validate_error_diagnostic(agent["error_diagnostic"])
    _require(type(agent["started"]) is bool, "agent.started 无效")
    for field in ("stop_reason", "state_status", "error_kind"):
        _require(agent[field] is None or (isinstance(agent[field], str) and len(agent[field]) <= 120), f"agent.{field} 无效")
    for field in ("llm_calls", "successful_responses", "tool_calls", "permission_denials", "input_tokens", "output_tokens", "duration_ms", "invalid_repeat_count"):
        _require(agent[field] is None or (type(agent[field]) is int and agent[field] >= 0), f"agent.{field} 无效")
    _require(agent["subagent_calls"] == 0, "比较 worker 不允许子代理调用")
    _require(agent["token_source"] in {"provider", "estimated", "mixed", "unavailable", "fixture"}, "agent.token_source 无效")
    outcomes = _object(agent["tool_outcomes"], {"succeeded", "failed", "denied", "invalid", "timeout"}, "agent.tool_outcomes")
    for count in outcomes.values():
        _require(type(count) is int and count >= 0, "agent.tool_outcomes 计数无效")

    grader = _object(trial["grader"], {"passed", "error_kind", "duration_ms", "exit_code", "result"}, "grader")
    _require(grader["passed"] is None or type(grader["passed"]) is bool, "grader.passed 无效")
    _require(grader["error_kind"] is None or (isinstance(grader["error_kind"], str) and len(grader["error_kind"]) <= 120), "grader.error_kind 无效")
    _require(grader["duration_ms"] is None or (type(grader["duration_ms"]) is int and grader["duration_ms"] >= 0), "grader.duration_ms 无效")
    _require(grader["exit_code"] is None or type(grader["exit_code"]) is int, "grader.exit_code 无效")
    if grader["result"] is not None:
        _require(isinstance(grader["result"], dict), "grader.result 必须是 object 或 null")
        _require(len(json.dumps(grader["result"], ensure_ascii=False).encode("utf-8")) <= 32 * 1024, "grader.result 超过 32 KiB")
    cleanup = _object(trial["cleanup"], {"complete", "issue"}, "cleanup")
    _require(type(cleanup["complete"]) is bool, "cleanup.complete 无效")
    _require(cleanup["issue"] is None or (isinstance(cleanup["issue"], str) and len(cleanup["issue"]) <= 300), "cleanup.issue 无效")
    evidence = _object(trial["evidence"], {"artifacts"}, "evidence")
    artifacts = evidence["artifacts"]
    _require(isinstance(artifacts, dict) and set(artifacts) == {"diff", "agent_log", "grader_log"}, "evidence.artifacts 不完整或含未知文件")
    for name, item in artifacts.items():
        artifact = _object(item, {"path", "sha256", "size_bytes"}, f"artifact.{name}")
        _safe_relpath(artifact["path"], f"artifact.{name}.path")
        _require(isinstance(artifact["sha256"], str) and _SHA256.fullmatch(artifact["sha256"]) is not None, f"artifact.{name}.sha256 无效")
        _require(type(artifact["size_bytes"]) is int and 0 <= artifact["size_bytes"] <= 1024 * 1024, f"artifact.{name}.size_bytes 无效")
    encoded = json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _require(len(encoded) <= MAX_COMPARISON_TRIAL_BYTES, "ComparisonTrial 超过大小上限")
    return ComparisonTrial(json.loads(json.dumps(trial, ensure_ascii=False, sort_keys=True)))


__all__ = [
    "COMPARISON_SCHEMA_VERSION", "MAX_COMPARISON_GROUPS", "MAX_COMPARISON_REPEATS",
    "MAX_COMPARISON_SLOTS", "MAX_COMPARISON_SPEC_BYTES", "MAX_COMPARISON_RUN_BYTES",
    "MAX_COMPARISON_TRIAL_BYTES", "PINNED_V052_REVISION", "COMPARISON_SPEC_SCHEMA_V1",
    "ComparisonSpec", "ComparisonRun", "ComparisonTrial", "canonical_json", "sha256_bytes", "comparison_execution_order",
    "sha256_json", "validate_comparison_spec", "validate_comparison_run",
    "validate_comparison_trial", "validate_error_diagnostic",
]
