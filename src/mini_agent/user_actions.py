"""Structured user-side state transitions shared by CLI and evaluation drivers.

The Runtime never receives this module as a tool. Callers represent an actual
CLI user action or a frozen evaluation feedback script, so model output cannot
manufacture user approval or crash-recovery decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Callable

from mini_agent.session import SessionCommitUncertainError, SessionError


@dataclass(frozen=True)
class UserActionResult:
    action: str
    status: str
    source: str
    record: dict[str, Any] | None = None
    session_id: str | None = None
    envelope: dict[str, Any] | None = None
    affected_ids: tuple[str, ...] = ()
    error_kind: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["affected_ids"] = list(self.affected_ids)
        return value


def _source_metadata(source: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    if source not in {"cli", "simulated_user"}:
        raise ValueError("user action source must be cli or simulated_user")
    safe: dict[str, Any] = {"source": source}
    if metadata is None:
        return safe
    if not isinstance(metadata, dict) or set(metadata) - {
        "script_sha256", "trigger", "revision_id", "issue_id", "accepted",
    }:
        raise ValueError("user action metadata contains unknown fields")
    script_digest = metadata.get("script_sha256")
    if script_digest is not None and (
        not isinstance(script_digest, str) or len(script_digest) != 64
        or any(char not in "0123456789abcdef" for char in script_digest)
    ):
        raise ValueError("simulated user script digest is invalid")
    trigger = metadata.get("trigger")
    if trigger is not None and (not isinstance(trigger, str) or len(trigger) > 120):
        raise ValueError("user action trigger is invalid")
    for key in ("revision_id", "issue_id"):
        value = metadata.get(key)
        if value is not None and (not isinstance(value, (str, int)) or isinstance(value, bool)):
            raise ValueError(f"user action {key} is invalid")
    accepted = metadata.get("accepted")
    if accepted is not None and not isinstance(accepted, bool):
        raise ValueError("user action accepted flag is invalid")
    safe.update(metadata)
    return safe


def resolve_crash_issue(
    state: Any, issue_id: str, decision: str, feedback: str, *,
    source: str = "cli", metadata: dict[str, Any] | None = None,
) -> UserActionResult:
    source_facts = _source_metadata(source, metadata)
    record = state.resolve_crash_issue(issue_id, decision, feedback)
    return UserActionResult(
        "resolve_crash_issue", "accepted", source,
        record=asdict(record) if hasattr(record, "__dataclass_fields__") else {
            "issue_id": issue_id, "decision": decision,
        }, affected_ids=(issue_id,),
        detail=json.dumps(source_facts, ensure_ascii=False, sort_keys=True),
    )


def decide_plan(
    state: Any, decision: str, revision_id: int, feedback: str | None = None, *,
    source: str = "cli", metadata: dict[str, Any] | None = None,
) -> UserActionResult:
    source_facts = _source_metadata(source, metadata)
    state.decide_plan(decision, revision_id, feedback)
    record = state.user_plan_decisions[-1] if getattr(state, "user_plan_decisions", None) else None
    return UserActionResult(
        "decide_plan", "accepted", source,
        record=asdict(record) if record is not None else {
            "revision_id": revision_id, "decision": decision,
        }, affected_ids=(str(revision_id),),
        detail=json.dumps(source_facts, ensure_ascii=False, sort_keys=True),
    )


def review_plan(
    state: Any, revision_id: int, *, source: str = "cli",
    metadata: dict[str, Any] | None = None,
) -> UserActionResult:
    source_facts = _source_metadata(source, metadata)
    state.review_current_plan(revision_id)
    return UserActionResult(
        "review_plan", "accepted", source,
        record={"revision_id": revision_id}, affected_ids=(str(revision_id),),
        detail=json.dumps(source_facts, ensure_ascii=False, sort_keys=True),
    )


def save_safe_point(
    *, state: Any, context: Any, store: Any, session_id: str | None,
    workspace_root: str, handoff_status: str = "active",
    manager: Any = None, sync_processes: Callable[[], Any] | None = None,
) -> UserActionResult:
    """Run the CLI safe-point preconditions and atomic save without rendering."""
    if not getattr(state, "task", ""):
        return UserActionResult("save", "rejected", "cli", error_kind="no_active_task")
    active = list(getattr(state, "active_delegation_records", []))
    if active:
        ids = tuple(
            str(getattr(item, "subagent_id", "-"))
            if getattr(item, "mode", "synchronous") == "background"
            else str(getattr(item, "delegation_id", "-"))
            for item in active[:16]
        )
        return UserActionResult("save", "rejected", "cli", affected_ids=ids,
                                error_kind="active_subagent")
    if sync_processes is not None:
        sync_processes()
    try:
        child_sessions = (
            manager.export_child_sessions(state)
            if manager is not None and hasattr(manager, "export_child_sessions") else []
        )
    except ValueError as error:
        return UserActionResult("save", "rejected", "cli", error_kind=type(error).__name__,
                                detail=str(error)[:500])
    try:
        envelope = store.save(
            session_id, state, context, workspace_root=workspace_root,
            handoff_status=handoff_status, save_kind="safe_point",
            child_sessions=child_sessions,
        )
    except SessionCommitUncertainError as error:
        return UserActionResult("save", "uncertain", "cli", session_id=error.session_id,
                                error_kind=type(error).__name__, detail=str(error)[:500])
    except SessionError as error:
        return UserActionResult("save", "failed", "cli", session_id=session_id,
                                error_kind=type(error).__name__, detail=str(error)[:500])
    return UserActionResult("save", "accepted", "cli", session_id=envelope["session_id"],
                            envelope=envelope)


def script_digest(raw_script: bytes) -> str:
    return hashlib.sha256(raw_script).hexdigest()


__all__ = [
    "UserActionResult", "decide_plan", "resolve_crash_issue", "review_plan",
    "save_safe_point", "script_digest",
]
