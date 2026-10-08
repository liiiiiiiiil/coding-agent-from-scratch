"""Persistable parent-task request reservations (standard library only).

Estimates are admission policy, never a claim about the provider tokenizer.
Only provider observations warm calibration; an unknown request costs its
entire reservation. The sequential request ledger makes settlement idempotent.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json


def request_input_estimate(messages, schemas=(), *, model_id="", stream=True,
                           max_output_tokens=1024):
    """Bounded surrogate for the complete OpenAI Chat request JSON.

    UTF-8 size includes roles, object keys, punctuation and tool schemas. It
    is not a tokenizer or a promise about hidden provider-side context.
    The 10% margin is explicit and provider observations still calibrate it.
    No request text or model ID is retained by the budget ledger.
    """
    payload = {"model": model_id, "messages": messages, "stream": bool(stream),
               "max_tokens": max_output_tokens}
    if schemas:
        payload["tools"] = list(schemas)
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return (size * 11 + 29) // 30


class BudgetPersistenceError(RuntimeError):
    """A request ledger could not be committed; no further I/O is permitted."""


class BudgetUnavailable(RuntimeError):
    """The protected request floor cannot fit the remaining task budget."""


REQUEST_POLICY = {
    "cold_samples": 2, "cold_input_multiplier": 2,
    "warm_positive_error_samples": 8, "warm_margin_percent": 10,
    "input_allowance_tokens": 256, "minimum_output_tokens": 256,
    "work_output_tokens": 1024, "summary_output_tokens": 512,
    "final_output_tokens": 512, "recent_complete_rounds": 2,
    "calibration_scope": "task_binding_schema_request_kind",
}

def request_key(binding_ref, schemas, kind, *, estimator=None):
    # Only a digest survives: no endpoint, model ID, credential or text.
    source = binding_ref.to_dict() if hasattr(binding_ref, "to_dict") else binding_ref
    if source is not None and not isinstance(source, (dict, str)):
        source = getattr(source, "fingerprint", None)
    identity = [source, schemas, kind]
    if estimator is not None:
        identity.append(estimator)
    return sha256(json.dumps(identity, sort_keys=True,
                             ensure_ascii=False).encode()).hexdigest()


def validate_snapshot(value):
    fields = {"version", "limit", "input_tokens", "output_tokens", "unknown_requests",
              "next_request_id", "last_settled_id", "pending", "samples", "overrun_tokens"}
    if not isinstance(value, dict) or set(value) != fields or value["version"] != 1:
        raise ValueError("invalid task budget ledger")
    for key in fields - {"pending", "samples"}:
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("invalid task budget counter")
    if value["limit"] <= 0 or value["next_request_id"] != value["last_settled_id"] + 1:
        raise ValueError("invalid task budget sequence")
    if (value["unknown_requests"] > value["last_settled_id"]
            or value["overrun_tokens"] < value["input_tokens"] + value["output_tokens"] - value["limit"]):
        raise ValueError("inconsistent task budget accounting")
    samples = value["samples"]
    if not isinstance(samples, dict) or len(samples) > 16:
        raise ValueError("invalid task budget calibration")
    for key, observations in samples.items():
        if (not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key)
                or not isinstance(observations, list) or len(observations) > 8):
            raise ValueError("invalid task budget calibration")
        for row in observations:
            if (not isinstance(row, list) or len(row) != 2
                    or any(type(n) is not int or n < 0 for n in row)):
                raise ValueError("invalid task budget observation")
    pending = value["pending"]
    if pending is not None:
        if (not isinstance(pending, dict)
                or set(pending) != {"request_id", "key", "raw_input", "input", "output", "kind"}
                or pending["request_id"] != value["next_request_id"]
                or pending["key"] not in samples
                or pending["kind"] not in {"work", "summary", "final"}
                or any(type(pending[k]) is not int or pending[k] < 0
                       for k in ("request_id", "raw_input", "input", "output"))):
            raise ValueError("invalid pending task budget reservation")
        if (pending["input"] < 256 or pending["output"] <= 0
                or pending["output"] > REQUEST_POLICY[f'{pending["kind"]}_output_tokens']
                or value["input_tokens"] + value["output_tokens"] + pending["input"] + pending["output"] > value["limit"]):
            raise ValueError("invalid pending task budget amounts")


class TaskBudgetController:
    def __init__(self, limit=None, *, snapshot=None):
        if snapshot is None:
            if type(limit) is not int or limit <= 0:
                raise ValueError("task token budget must be a positive integer")
            snapshot = dict(version=1, limit=limit, input_tokens=0, output_tokens=0,
                            unknown_requests=0, next_request_id=1, last_settled_id=0,
                            pending=None, samples={}, overrun_tokens=0)
        validate_snapshot(snapshot)
        self.data = deepcopy(snapshot)

    @property
    def used(self):
        return self.data["input_tokens"] + self.data["output_tokens"]

    @property
    def remaining(self):
        pending = self.data["pending"]
        return max(0, self.data["limit"] - self.used
                   - (pending["input"] + pending["output"] if pending else 0))

    def snapshot(self):
        return deepcopy(self.data)

    def input_reservation(self, raw, key):
        samples = self.data["samples"].get(key, [])
        if len(samples) < 2:
            return raw * 2 + 256
        error = max(max(0, actual - estimate) for estimate, actual in samples)
        return ((raw + error) * 110 + 99) // 100 + 256

    def input_allowance(self, key, output, closing=0):
        available = self.remaining - output - closing
        low, high = 0, max(0, available)
        while low < high:
            mid = (low + high + 1) // 2
            if self.input_reservation(mid, key) <= available:
                low = mid
            else:
                high = mid - 1
        return low

    def proposal(self, raw, key, kind="work", maximum_output=8192, closing=0):
        cap = min(maximum_output, REQUEST_POLICY[f'{kind}_output_tokens'])
        reserved = self.input_reservation(raw, key)
        output = min(cap, max(0, self.remaining - reserved - closing))
        minimum = min(maximum_output, 256)
        return {"raw_input": raw, "input": reserved, "output": output,
                "key": key, "kind": kind,
                "admitted": output >= minimum and not self.data["overrun_tokens"]}

    def reserve(self, proposal):
        if self.data["pending"] is not None or not proposal["admitted"]:
            raise BudgetUnavailable("insufficient request budget")
        key = proposal["key"]
        if key not in self.data["samples"]:
            # Bounded calibration; a new binding/schema is cold.
            if len(self.data["samples"]) >= 16:
                del self.data["samples"][next(iter(self.data["samples"]))]
            self.data["samples"][key] = []
        pending = {k: proposal[k] for k in ("key", "kind", "raw_input", "input", "output")}
        pending["request_id"] = self.data["next_request_id"]
        self.data["pending"] = pending
        return pending["request_id"]

    def settle(self, request_id, input_tokens=None, output_tokens=None, *, provider=False):
        if type(request_id) is not int or request_id <= 0:
            raise ValueError("invalid request id")
        if request_id <= self.data["last_settled_id"]:
            return False
        pending = self.data["pending"]
        if pending is None or pending["request_id"] != request_id:
            raise ValueError("request settlement does not match reservation")
        if not provider:
            input_tokens, output_tokens = pending["input"], pending["output"]
            self.data["unknown_requests"] += 1
        elif any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("invalid provider usage")
        if provider:
            samples = self.data["samples"][pending["key"]]
            samples.append([pending["raw_input"], input_tokens])
            del samples[:-8]
        self.data["input_tokens"] += input_tokens
        self.data["output_tokens"] += output_tokens
        self.data["overrun_tokens"] = max(
            self.data["overrun_tokens"], input_tokens - pending["input"],
            output_tokens - pending["output"], self.used - self.data["limit"], 0)
        self.data["last_settled_id"] = request_id
        self.data["next_request_id"] = request_id + 1
        self.data["pending"] = None
        return True

    def reconcile_pending(self):
        pending = self.data["pending"]
        if pending:
            self.settle(pending["request_id"])
            return True
        return False
