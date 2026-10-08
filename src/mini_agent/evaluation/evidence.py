"""Bounded, sanitized evidence helpers for reliability trial artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


MAX_EVIDENCE_BYTES = 64 * 1024
MAX_EVIDENCE_FILES = 64
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;\"']+"), r"\1<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_./+=-]{8,}"), "<redacted-key>"),
)


def sanitize_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    text = value
    for secret in sorted((item for item in secrets if len(item) >= 3), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def safe_json(value: Any, *, secrets: tuple[str, ...] = (), depth: int = 0) -> Any:
    if depth > 12:
        return "<depth-limit>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value[:4000], secrets)
    if isinstance(value, list):
        return [safe_json(item, secrets=secrets, depth=depth + 1) for item in value[:128]]
    if isinstance(value, dict):
        return {
            sanitize_text(str(key)[:100], secrets): safe_json(item, secrets=secrets, depth=depth + 1)
            for key, item in list(value.items())[:128]
        }
    return f"<{type(value).__name__}>"


def write_evidence(root: Path, relative: str, value: Any, *, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise ValueError("evidence_path_invalid")
    rendered = json.dumps(safe_json(value, secrets=secrets), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(rendered) > MAX_EVIDENCE_BYTES:
        rendered = rendered[:MAX_EVIDENCE_BYTES]
        while True:
            try:
                rendered.decode("utf-8")
                break
            except UnicodeDecodeError:
                rendered = rendered[:-1]
    destination = (root / path).resolve(strict=False)
    root_resolved = root.resolve(strict=True)
    if destination != root_resolved and root_resolved not in destination.parents:
        raise ValueError("evidence_path_escape")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    return {"path": path.as_posix(), "size": len(rendered), "sha256": hashlib.sha256(rendered).hexdigest()}


def inspect_evidence(root: Path, references: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    verified: list[dict[str, Any]] = []
    incomplete: list[str] = []
    if len(references) > MAX_EVIDENCE_FILES:
        return [], ["evidence_reference_limit"]
    for item in references:
        rel = item.get("path") if isinstance(item, dict) else None
        if not isinstance(rel, str):
            incomplete.append("invalid_reference")
            continue
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts:
            incomplete.append("path_escape")
            continue
        target = root / path
        try:
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVIDENCE_BYTES:
                raise ValueError
            raw = target.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError):
            incomplete.append("missing_or_invalid_artifact")
            continue
        if digest != item.get("sha256"):
            incomplete.append("evidence_digest_mismatch")
            continue
        verified.append({"path": path.as_posix(), "size": len(raw), "sha256": digest})
    return verified, incomplete


__all__ = ["inspect_evidence", "safe_json", "sanitize_text", "write_evidence"]
