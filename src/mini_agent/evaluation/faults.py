"""Finite, named fault drivers used by reliability evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Any, Callable


MAX_FAULT_EVENTS = 64
MAX_EVENT_BYTES = 2048
FAULT_DRIVER_IDS = frozenset({
    "tool-handler-exception", "permission-denied", "verification-repair",
    "process-wait-timeout", "crash-before-admission", "crash-after-admission",
    "crash-after-handler", "durable-commit-failure", "recovery-user-decisions",
    "mcp-disconnect", "mcp-remote-error", "mcp-is-error", "mcp-call-timeout",
    "subagent-timeout", "subagent-cancel", "subagent-unclaimed",
    "subagent-interrupted", "followup-incompatible",
})


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FaultEvent:
    sequence: int
    fault_id: str
    target: str
    outcome: str
    occurrence: int
    evidence_sha256: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence, "fault_id": self.fault_id,
            "target": self.target, "outcome": self.outcome,
            "occurrence": self.occurrence, "evidence_sha256": self.evidence_sha256,
            "detail": self.detail[:256],
        }


class FaultController:
    """Own one frozen driver and record only observed injection hits."""

    def __init__(self, fault: dict[str, Any]) -> None:
        fault_id = fault.get("fault_id")
        if fault_id not in FAULT_DRIVER_IDS:
            raise ValueError("unknown_fault_driver")
        self.fault_id = fault_id
        self.target = str(fault.get("target", ""))[:96]
        self.occurrences = int(fault.get("occurrences", 1))
        if not 1 <= self.occurrences <= 8:
            raise ValueError("fault_occurrences_out_of_range")
        self.parameters = dict(fault.get("parameters", {}))
        self._lock = threading.Lock()
        self._hits = 0
        self._events: list[FaultEvent] = []
        self._barrier = threading.Event()
        self._cancel = threading.Event()

    @property
    def hit_count(self) -> int:
        with self._lock:
            return self._hits

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(event.to_dict() for event in self._events)

    def hit(self, target: str, *, outcome: str = "triggered", evidence: Any = None, detail: str = "") -> bool:
        with self._lock:
            if len(self._events) >= MAX_FAULT_EVENTS or target != self.target or self._hits >= self.occurrences:
                return False
            self._hits += 1
            event = FaultEvent(
                len(self._events) + 1, self.fault_id, target, outcome, self._hits,
                _digest(evidence if evidence is not None else {"fault_id": self.fault_id, "target": target}),
                detail[:256],
            )
            encoded = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_EVENT_BYTES:
                raise ValueError("fault_event_too_large")
            self._events.append(event)
            return True

    def wrap_handler(self, tool_name: str, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Raise once immediately before the first matching handler body."""
        def wrapped(**kwargs):
            expected = self.parameters.get("tool", tool_name)
            if self.fault_id == "tool-handler-exception" and tool_name == expected:
                if self.hit(self.target, evidence={"tool": tool_name, "arguments_sha256": _digest(kwargs)}):
                    raise RuntimeError("injected_handler_failure")
            return handler(**kwargs)
        return wrapped

    def durable_commit_hook(self, boundary: str) -> None:
        """Call at a real commit boundary; never alters State or synthesizes a result."""
        if self.fault_id != "durable-commit-failure":
            return
        requested = self.parameters.get("boundary")
        if requested == boundary and self.hit(self.target, evidence={"boundary": boundary}):
            raise OSError("injected_durable_commit_failure")

    def barrier(self, target: str, timeout: float) -> bool:
        """A bounded worker barrier; supervisor can release or request cancellation."""
        if target != self.target or timeout <= 0:
            return False
        if not self.hit(target, evidence={"barrier": target}):
            return False
        return self._barrier.wait(timeout)

    def release_barrier(self) -> None:
        self._barrier.set()

    def request_cancel(self) -> None:
        self._cancel.set()
        self._barrier.set()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel.is_set()

    def wrap_client(self, client: Callable[..., Any]) -> Callable[..., Any]:
        """Inject a child-call timeout before network/model I/O."""
        def wrapped(*args, **kwargs):
            if self.fault_id == "subagent-timeout" and self.hit(self.target, evidence={"call": "child_model"}):
                raise TimeoutError("injected_subagent_timeout")
            return client(*args, **kwargs)
        return wrapped

    def snapshot(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "target": self.target,
            "planned_occurrences": self.occurrences,
            "hits": self.hit_count,
            "events": list(self.events),
            "status": "triggered" if self.hit_count else "not_triggered",
        }


__all__ = ["FAULT_DRIVER_IDS", "FaultController", "FaultEvent"]
