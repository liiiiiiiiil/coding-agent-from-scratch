"""Frozen, bounded contracts for reliability fault-injection evaluations.

This format is intentionally independent from the coding benchmark's TrialResult.
All executable behavior is selected from finite IDs in this module; manifests can
name a grader or fault driver, but cannot import modules or provide code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


RELIABILITY_FORMAT = "mini_agent.reliability"
SCHEMA_VERSION = 1
SUITE_ID = "reliability-boundaries"
SUITE_VERSION = "1.6"
MAX_SUITE_BYTES = 128 * 1024
MAX_SCENARIO_BYTES = 64 * 1024
MAX_MATERIAL_FILES = 64
MAX_MATERIAL_BYTES = 2 * 1024 * 1024
MAX_TASK_CHARS = 8000
MAX_FEEDBACK_EVENTS = 32
MAX_TRIAL_BYTES = 256 * 1024
MAX_LOG_BYTES = 64 * 1024

SCENARIO_IDS = (
    "tool-handler-exception", "permission-denied", "verification-repair",
    "process-wait-timeout", "crash-before-admission", "crash-after-admission",
    "crash-after-handler", "durable-commit-failure", "recovery-user-decisions",
    "mcp-disconnect", "mcp-remote-error", "mcp-is-error", "mcp-call-timeout",
    "subagent-timeout", "subagent-cancel", "subagent-unclaimed",
    "subagent-interrupted", "followup-incompatible",
)
LIVE_SCENARIO_IDS = (
    "tool-handler-exception", "permission-denied", "verification-repair",
    "process-wait-timeout", "crash-after-handler", "mcp-disconnect",
    "subagent-timeout",
)
FAULT_IDS = frozenset({
    "tool-handler-exception", "permission-denied", "verification-repair",
    "process-wait-timeout", "crash-before-admission", "crash-after-admission",
    "crash-after-handler", "durable-commit-failure", "recovery-user-decisions",
    "mcp-disconnect", "mcp-remote-error", "mcp-is-error", "mcp-call-timeout",
    "subagent-timeout", "subagent-cancel", "subagent-unclaimed",
    "subagent-interrupted", "followup-incompatible",
})
TRANSPORTS = frozenset({"stdio", "loopback_http"})
GRADER_IDS = frozenset({
    "tool-handler-exception", "permission-denied", "verification-repair",
    "process-wait-timeout", "crash-before-admission", "crash-after-admission",
    "crash-after-handler", "durable-commit-failure", "recovery-user-decisions",
    "mcp-disconnect", "mcp-remote-error", "mcp-is-error", "mcp-call-timeout",
    "subagent-timeout", "subagent-cancel", "subagent-unclaimed",
    "subagent-interrupted", "followup-incompatible",
})
GRADER_CHECK_IDS = frozenset({
    "fault_triggered", "invariants_match_evidence", "cleanup_complete",
    "task-transform-doubles",
})
INVARIANT_IDS = frozenset({
    "tool-result-replayed", "denied-handler-not-run", "no-implicit-approval",
    "verification-current-generation", "hidden-grader-passes", "timeout-not-exit",
    "active-process-blocks-done", "cleanup-complete", "not-executed-recorded",
    "admitted-call-not-replayed", "source-session-read-only", "side-effect-once",
    "per-call-order-preserved", "no-action-after-commit-failure",
    "invalid-user-feedback-rejected", "decision-source-recorded", "block-stops-task",
    "single-source-claim", "connection-not-reused", "no-call-retry",
    "integer-error-sanitized", "valid-is-error-result", "bounded-timeout",
    "failed-subagent-claimed", "usage-settled-once", "cooperative-cancel",
    "result-available-after-cancel", "unclaimed-blocks-done", "safe-point-blocked",
    "repeat-claim-stable", "interrupted-no-worker", "unknown-usage-conservative",
    "issue-explicitly-resolved", "skill-change-rejected-before-llm",
    "other-child-session-unaffected", "permission-stops-task",
})
RECOVERY_STEPS = frozenset({
    "inspect_evidence", "claim_new_session", "resolve_issues", "replan",
    "reauthorize", "verify_current_generation", "stop_process", "sync_process",
    "cleanup_resources", "claim_child_result", "continue_parent_investigation",
})
FEEDBACK_DECISIONS = frozenset({
    "investigate", "continue", "block", "approve", "reject", "continue_exploring",
})
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RULE_ACTIONS = {"allow", "deny", "ask"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_object(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON object")
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ValueError(f"{label} 缺少字段: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{label} 含未知字段: {', '.join(sorted(unknown))}")
    return value


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} 必须是 {minimum}..{maximum} 的整数")
    return value


def safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise ValueError(f"{label} 必须是有界相对路径")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} 不得包含绝对路径或路径穿越")
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{label} 路径格式无效")
    return path.as_posix()


def _read_regular(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError(f"{path.name} 必须是有界普通文件")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("评测材料在读取时发生变化")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("评测材料超过大小限制")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _reject_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ValueError("评测路径不得经过符号链接")
        except FileNotFoundError:
            break


def _validate_grader_source(path: Path, expected_id: str) -> dict[str, Any]:
    """Accept only a literal GRADER assignment; no imports or executable code."""
    try:
        source = _read_regular(path, MAX_SCENARIO_BYTES).decode("utf-8")
        tree = ast.parse(source, filename=path.name, mode="exec")
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ValueError("grader.py 必须是有效 UTF-8 字面量合同") from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        raise ValueError("grader.py 只能包含一个 GRADER 字面量赋值")
    statement = tree.body[0]
    if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name) or statement.targets[0].id != "GRADER":
        raise ValueError("grader.py 只能定义 GRADER 字段")
    try:
        value = ast.literal_eval(statement.value)
    except (ValueError, TypeError, MemoryError, RecursionError) as error:
        raise ValueError("grader.py 不得包含执行代码") from error
    if not isinstance(value, dict) or set(value) != {"grader_id", "checks"}:
        raise ValueError("GRADER 必须含 grader_id 和 checks")
    if value["grader_id"] != expected_id or not isinstance(value["checks"], list) or not value["checks"]:
        raise ValueError("GRADER 标识或 checks 无效")
    if (any(not isinstance(item, str) or item not in GRADER_CHECK_IDS for item in value["checks"])
            or len(value["checks"]) != len(set(value["checks"]))
            or not {"fault_triggered", "invariants_match_evidence", "cleanup_complete"}.issubset(value["checks"])):
        raise ValueError("GRADER checks 必须是已登记的边界/任务检查 ID")
    return value


def material_tree_sha256(root: Path) -> str:
    root = root.absolute()
    entries: list[dict[str, Any]] = []
    count = total = 0
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        # Import caches are generated while fixture files are executed, so
        # they are not part of the frozen task inputs.
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        names.sort()
        for name in dirs:
            if stat.S_ISLNK((Path(current) / name).lstat().st_mode):
                raise ValueError("评测材料不得包含符号链接")
        for name in names:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            safe_relative(relative, "材料路径")
            raw = _read_regular(path, MAX_MATERIAL_BYTES)
            count += 1
            total += len(raw)
            if count > MAX_MATERIAL_FILES or total > MAX_MATERIAL_BYTES:
                raise ValueError("场景材料超过文件数或总大小限制")
            entries.append({"path": relative, "size": len(raw), "sha256": sha256(raw)})
    return sha256(canonical_json(entries))


@dataclass(frozen=True)
class ReliabilityScenario:
    scenario_id: str
    task: str
    initial_dir: str
    allowed_tools: tuple[str, ...]
    permission_rules: tuple[dict[str, str], ...]
    budget: dict[str, int]
    fault: dict[str, Any]
    invariants: tuple[str, ...]
    recovery_steps: tuple[str, ...]
    feedback_strategy: tuple[dict[str, str], ...]
    grader_id: str
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = SCHEMA_VERSION
        value["allowed_tools"] = list(self.allowed_tools)
        value["permission_rules"] = [dict(x) for x in self.permission_rules]
        value["invariants"] = list(self.invariants)
        value["recovery_steps"] = list(self.recovery_steps)
        value["feedback_strategy"] = [dict(x) for x in self.feedback_strategy]
        return value


@dataclass(frozen=True)
class ReliabilitySuite:
    suite_id: str
    version: str
    description: str
    default_repeats: int
    offline_repeats: int
    suite_sha256: str
    support_sha256: str
    suite_path: Path
    scenario_entries: tuple[dict[str, str], ...]
    scenarios: tuple[ReliabilityScenario, ...]
    live_scenario_ids: tuple[str, ...]


def validate_scenario(raw: Any, *, expected_id: str | None = None) -> ReliabilityScenario:
    obj = _strict_object(
        raw,
        {"schema_version", "scenario_id", "version", "task", "initial_dir", "allowed_tools",
         "permission_rules", "budget", "fault", "invariants", "recovery_steps", "feedback_strategy", "grader_id"},
        set(), "scenario",
    )
    if obj["schema_version"] != SCHEMA_VERSION:
        raise ValueError("scenario schema_version 不支持")
    sid = obj["scenario_id"]
    if not isinstance(sid, str) or sid not in SCENARIO_IDS or (expected_id is not None and sid != expected_id):
        raise ValueError("scenario_id 不在冻结场景集中或与目录不一致")
    version = obj["version"]
    if version not in {"1.0", "1.1", "1.2", "1.3", "1.4", "1.5", SUITE_VERSION}:
        raise ValueError("scenario version 必须为已支持的冻结版本")
    task = obj["task"]
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
        raise ValueError("task 必须是非空且有界文本")
    initial_dir = safe_relative(obj["initial_dir"], "initial_dir")
    tools = obj["allowed_tools"]
    known_tools = {
        "read_file", "list_dir", "grep", "write_file", "edit_file", "calculate", "run_shell",
        "start_process", "get_process", "read_process", "list_processes", "wait_process",
        "write_process", "terminate_process", "kill_process", "delegate_task", "spawn_subagent",
        "followup_subagent", "get_subagent_status", "get_subagent_result", "cancel_subagent",
        "mcp_resource", "mcp_prompt", "mcp_reliability_echo",
        "begin_plan", "commit_plan", "request_replan", "update_plan_progress", "recover",
    }
    if not isinstance(tools, list) or len(tools) > 32 or any(not isinstance(x, str) or x not in known_tools for x in tools) or len(tools) != len(set(tools)):
        raise ValueError("allowed_tools 含未知、重复或过多工具")
    rules = obj["permission_rules"]
    if not isinstance(rules, list) or len(rules) > 64:
        raise ValueError("permission_rules 必须是有界列表")
    normalized_rules: list[dict[str, str]] = []
    for item in rules:
        rule = _strict_object(item, {"tool", "pattern", "action"}, set(), "permission rule")
        if rule["tool"] not in known_tools or rule["action"] not in _RULE_ACTIONS:
            raise ValueError("permission rule 的工具或动作无效")
        pattern = rule["pattern"]
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512 or "\x00" in pattern:
            raise ValueError("permission rule pattern 无效")
        normalized_rules.append({"tool": rule["tool"], "pattern": pattern, "action": rule["action"]})
    budget = _strict_object(obj["budget"], {"max_rounds", "tool_calls", "wall_seconds", "grader_seconds", "parent_tokens", "child_tokens"}, set(), "budget")
    budget_caps = {"max_rounds": (1, 20), "tool_calls": (1, 48), "wall_seconds": (1, 240), "grader_seconds": (1, 30), "parent_tokens": (1, 64000), "child_tokens": (0, 32000)}
    normalized_budget = {key: _bounded_int(budget[key], key, *limits) for key, limits in budget_caps.items()}
    fault = _strict_object(obj["fault"], {"fault_id", "target", "occurrences", "parameters"}, set(), "fault")
    if fault["fault_id"] != sid or fault["fault_id"] not in FAULT_IDS:
        raise ValueError("fault_id 必须选择本场景对应的具名驱动")
    if (not isinstance(fault["target"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_./:-]{0,95}", fault["target"])
            or ".." in fault["target"] or "//" in fault["target"]):
        raise ValueError("fault target 无效")
    occurrences = _bounded_int(fault["occurrences"], "fault occurrences", 1, 8)
    parameters = fault["parameters"]
    if not isinstance(parameters, dict) or len(parameters) > 16 or any(not isinstance(k, str) or not isinstance(v, (str, int, bool, list, dict, type(None))) for k, v in parameters.items()):
        raise ValueError("fault parameters 必须是有界 JSON object")
    allowed_parameter_keys = {"tool", "transport", "bind", "variant_values", "boundary", "decision_path", "timeout_ms", "child_budget"}
    if set(parameters) - allowed_parameter_keys:
        raise ValueError("fault parameters 含未知字段")
    if "child_budget" in parameters:
        child_budget = parameters["child_budget"]
        if (sid != "subagent-timeout" or not isinstance(child_budget, dict)
                or any(type(value) is not int for value in child_budget.values())
                or child_budget != {
            "max_rounds": 1, "max_llm_calls": 1, "max_tool_calls": 1,
            "max_tokens": 32000, "timeout_seconds": 30,
        }):
            raise ValueError("child_budget 必须使用冻结的超时注入预算")
    if sid == "subagent-timeout" and version in {"1.5", SUITE_VERSION} and "child_budget" not in parameters:
        raise ValueError("超时场景缺少冻结 child_budget")
    variants = parameters.get("variant_values", {})
    if not isinstance(variants, dict) or set(variants) - {"transport", "boundary", "decision_path"}:
        raise ValueError("variant_values 只允许运输、提交边界或用户决定轴")
    for axis, values in variants.items():
        allowed_values = {
            "transport": TRANSPORTS,
            "boundary": {"admission", "per_call", "round_commit"},
            "decision_path": {"continue", "block"},
        }[axis]
        if not isinstance(values, list) or not values or any(value not in allowed_values for value in values) or len(values) != len(set(values)):
            raise ValueError(f"{axis} parameter variants 无效")
    if sid.startswith("mcp-"):
        transports = variants.get("transport", [parameters.get("transport")])
        if any(transport not in TRANSPORTS for transport in transports):
            raise ValueError("MCP 场景必须分别声明 stdio 或 loopback_http")
        if "loopback_http" in transports and parameters.get("bind") != "127.0.0.1":
            raise ValueError("MCP HTTP fixture 只能绑定显式 loopback 127.0.0.1")
    invariants = obj["invariants"]
    if not isinstance(invariants, list) or not invariants or len(invariants) > 32 or any(x not in INVARIANT_IDS for x in invariants) or len(set(invariants)) != len(invariants):
        raise ValueError("invariants 含未知或重复 ID")
    recovery = obj["recovery_steps"]
    if not isinstance(recovery, list) or len(recovery) > 16 or any(x not in RECOVERY_STEPS for x in recovery) or len(set(recovery)) != len(recovery):
        raise ValueError("recovery_steps 含未知或重复 ID")
    feedback = obj["feedback_strategy"]
    if not isinstance(feedback, list) or len(feedback) > MAX_FEEDBACK_EVENTS:
        raise ValueError("feedback_strategy 超过上限")
    normalized_feedback: list[dict[str, str]] = []
    for item in feedback:
        event = _strict_object(item, {"when", "decision", "feedback", "issue_selector"}, set(), "feedback event")
        if event["decision"] not in FEEDBACK_DECISIONS or not isinstance(event["when"], str) or len(event["when"]) > 120:
            raise ValueError("feedback event trigger 或 decision 无效")
        if not isinstance(event["feedback"], str) or len(event["feedback"]) > 1000:
            raise ValueError("feedback text 无效")
        if event["issue_selector"] != "first_unresolved" and event["issue_selector"] != "current_revision":
            raise ValueError("feedback 只能引用当前未决 issue 或 revision")
        normalized_feedback.append({k: event[k] for k in ("when", "decision", "feedback", "issue_selector")})
    grader = obj["grader_id"]
    if grader not in GRADER_IDS or grader != sid:
        raise ValueError("grader_id 必须选择本场景固定 grader")
    return ReliabilityScenario(
        sid, task, initial_dir, tuple(tools), tuple(normalized_rules), normalized_budget,
        {"fault_id": sid, "target": fault["target"], "occurrences": occurrences, "parameters": dict(parameters)},
        tuple(invariants), tuple(recovery), tuple(normalized_feedback), grader, version,
    )


def load_suite(path: str | os.PathLike[str], *, require_pinned: bool = True) -> ReliabilitySuite:
    suite_path = Path(path).expanduser().absolute()
    _reject_symlink_ancestors(suite_path)
    raw_bytes = _read_regular(suite_path, MAX_SUITE_BYTES)
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取可靠性 suite: {type(error).__name__}") from error
    obj = _strict_object(raw, {"format", "schema_version", "suite_id", "version", "description", "default_repeats", "offline_repeats", "live_scenario_ids", "support", "scenarios", "suite_sha256"}, {"request_budget_policy"}, "reliability suite")
    if obj["format"] != RELIABILITY_FORMAT or obj["schema_version"] != SCHEMA_VERSION:
        raise ValueError("可靠性 suite format/schema 不支持")
    if obj["suite_id"] != SUITE_ID or obj["version"] not in {"1.0", "1.1", "1.2", "1.3", "1.4", "1.5", SUITE_VERSION}:
        raise ValueError("不支持该冻结 reliability-boundaries 版本")
    from mini_agent.evaluation.reliability_diagnostics import REQUEST_BUDGET_POLICY, LEGACY_REQUEST_BUDGET_POLICY
    if obj["version"] in {"1.5", SUITE_VERSION} and obj.get("request_budget_policy") != REQUEST_BUDGET_POLICY:
        raise ValueError("request_budget_policy 必须匹配当前冻结规则")
    if obj["version"] == "1.4" and obj.get("request_budget_policy") != LEGACY_REQUEST_BUDGET_POLICY:
        raise ValueError("历史 1.4 request_budget_policy 不匹配")
    if obj["version"] not in {"1.4", "1.5", SUITE_VERSION} and "request_budget_policy" in obj:
        raise ValueError("历史合同不含 request_budget_policy")
    digest = obj["suite_sha256"]
    body = dict(obj)
    body.pop("suite_sha256")
    calculated = sha256(canonical_json(body))
    if not isinstance(digest, str) or digest != calculated:
        raise ValueError("可靠性 suite 摘要不匹配")
    entries = obj["scenarios"]
    if not isinstance(entries, list) or [entry.get("scenario_id") for entry in entries if isinstance(entry, dict)] != list(SCENARIO_IDS):
        raise ValueError("场景必须按冻结矩阵顺序完整列出")
    suite_root = suite_path.parent
    scenarios: list[ReliabilityScenario] = []
    seen_paths: set[str] = set()
    normalized_entries: list[dict[str, str]] = []
    for entry in entries:
        entry = _strict_object(entry, {"scenario_id", "path", "materials_sha256"}, set(), "scenario entry")
        sid = entry["scenario_id"]
        rel = safe_relative(entry["path"], "scenario path")
        if rel in seen_paths:
            raise ValueError("scenario 路径重复")
        seen_paths.add(rel)
        directory = suite_root / rel
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("scenario 目录缺失或是符号链接")
        scenario_path = directory / "scenario.json"
        scenario_raw = json.loads(_read_regular(scenario_path, MAX_SCENARIO_BYTES).decode("utf-8"))
        scenario = validate_scenario(scenario_raw, expected_id=sid)
        if material_tree_sha256(directory) != entry["materials_sha256"]:
            raise ValueError(f"场景 {sid} 材料摘要不匹配")
        required_materials = {"scenario.json", "responses.json", "feedback.json", "grader.json"}
        if not required_materials.issubset({p.name for p in directory.iterdir() if p.is_file()}):
            raise ValueError(f"场景 {sid} 缺少冻结材料")
        for required_json in ("responses.json", "feedback.json", "grader.json"):
            decoded = json.loads(_read_regular(directory / required_json, MAX_SCENARIO_BYTES).decode("utf-8"))
            if not isinstance(decoded, (list, dict)):
                raise ValueError(f"场景 {sid} 的 {required_json} 格式无效")
        grader_contract = _validate_grader_source(directory / "grader.py", sid)
        grader_json = json.loads(_read_regular(directory / "grader.json", MAX_SCENARIO_BYTES).decode("utf-8"))
        if grader_contract != grader_json:
            raise ValueError(f"场景 {sid} grader.py 与 grader.json 不一致")
        feedback_json = json.loads(_read_regular(directory / "feedback.json", MAX_SCENARIO_BYTES).decode("utf-8"))
        if feedback_json != [dict(item) for item in scenario.feedback_strategy]:
            raise ValueError(f"场景 {sid} 反馈脚本与冻结合同不一致")
        initial = directory / scenario.initial_dir
        if initial.is_symlink() or not initial.is_dir():
            raise ValueError(f"场景 {sid} 初始工作区缺失")
        scenarios.append(scenario)
        normalized_entries.append({"scenario_id": sid, "path": rel, "materials_sha256": entry["materials_sha256"]})
    live_ids = obj["live_scenario_ids"]
    if tuple(live_ids) != LIVE_SCENARIO_IDS:
        raise ValueError("live 子集与冻结清单不一致")
    if _bounded_int(obj["default_repeats"], "default_repeats", 1, 10) != 3:
        raise ValueError("v1.0 live 重复次数固定为 3")
    if _bounded_int(obj["offline_repeats"], "offline_repeats", 1, 4) != 2:
        raise ValueError("v1.0 离线不变量重复次数固定为 2")
    if not isinstance(obj["description"], str) or len(obj["description"]) > 2000:
        raise ValueError("suite description 无效")
    support = _strict_object(obj["support"], {"path", "materials_sha256"}, set(), "support materials")
    support_rel = safe_relative(support["path"], "support path")
    if support_rel != "support":
        raise ValueError("v1.0 support 路径固定为 support")
    support_root = suite_root / support_rel
    if support_root.is_symlink() or not support_root.is_dir() or material_tree_sha256(support_root) != support["materials_sha256"]:
        raise ValueError("support materials 缺失或摘要不匹配")
    if require_pinned:
        from mini_agent.evaluation.reliability import PINNED_SUITE_FINGERPRINT
        if obj["version"] != SUITE_VERSION or digest != PINNED_SUITE_FINGERPRINT:
            raise ValueError("可靠性 suite ID/version 指纹尚未登记或不匹配")
    return ReliabilitySuite(
        obj["suite_id"], obj["version"], obj["description"], obj["default_repeats"],
        obj["offline_repeats"], digest, support["materials_sha256"], suite_path,
        tuple(normalized_entries), tuple(scenarios), tuple(live_ids),
    )


def validate_reliability_result(value: Any) -> dict[str, Any]:
    fields = {
        "format", "schema_version", "trial_id", "suite_id", "suite_version", "suite_sha256",
        "scenario_id", "scenario_sha256", "run_kind", "variant", "repetition", "status", "started_at", "ended_at",
        "fault_status", "fault_events", "invariant_status", "invariants", "recovery_status", "recovery_steps",
        "grader_passed", "agent_stop_reason", "agent_terminal_status", "cleanup_complete", "cleanup_issue",
        "infrastructure_error", "source_consistent", "phases", "artifacts", "manual_review",
        "feedback_events", "state_summary", "trace_summary", "diff_patch",
    }
    optional = {
        "code_revision", "code_revision_end", "runtime_fingerprint", "runtime_fingerprint_end",
        "model_binding_ref",
    }
    obj = _strict_object(value, fields, optional, "reliability trial result")
    if obj["format"] != RELIABILITY_FORMAT or obj["schema_version"] != SCHEMA_VERSION:
        raise ValueError("可靠性 trial format/schema 无效")
    if obj["run_kind"] not in {"fixture", "live"}:
        raise ValueError("run_kind 无效")
    if obj["fault_status"] not in {"triggered", "not_triggered", "injection_error"}:
        raise ValueError("fault_status 无效")
    if obj["invariant_status"] not in {"passed", "failed", "incomplete"}:
        raise ValueError("invariant_status 无效")
    if obj["recovery_status"] not in {"succeeded", "failed", "not_applicable", "not_triggered", "incomplete"}:
        raise ValueError("recovery_status 无效")
    if obj["grader_passed"] is not True and obj["grader_passed"] is not False and obj["grader_passed"] is not None:
        raise ValueError("grader_passed 无效")
    if not isinstance(obj["cleanup_complete"], bool) or not isinstance(obj["source_consistent"], bool):
        raise ValueError("cleanup/source 状态无效")
    for key in ("code_revision", "code_revision_end", "runtime_fingerprint", "runtime_fingerprint_end"):
        if key in obj and obj[key] is not None and (
            not isinstance(obj[key], str) or len(obj[key]) > 128
        ):
            raise ValueError(f"{key} 无效")
    if "model_binding_ref" in obj and obj["model_binding_ref"] is not None and not isinstance(obj["model_binding_ref"], dict):
        raise ValueError("model_binding_ref 必须是无凭据 object")
    for key in ("fault_events", "invariants", "recovery_steps", "phases", "artifacts", "manual_review", "feedback_events"):
        if not isinstance(obj[key], (list, dict)):
            raise ValueError(f"{key} 类型无效")
    for key in ("state_summary", "trace_summary"):
        if not isinstance(obj[key], dict):
            raise ValueError(f"{key} 必须是 object")
    if not isinstance(obj["diff_patch"], str) or len(obj["diff_patch"].encode("utf-8")) > MAX_LOG_BYTES:
        raise ValueError("diff_patch 无效或超过大小限制")
    return obj


__all__ = [
    "FAULT_IDS", "INVARIANT_IDS", "LIVE_SCENARIO_IDS", "RELIABILITY_FORMAT", "ReliabilityScenario",
    "ReliabilitySuite", "SCENARIO_IDS", "SCHEMA_VERSION", "canonical_json", "load_suite",
    "material_tree_sha256", "safe_relative", "sha256", "validate_reliability_result", "validate_scenario",
]
