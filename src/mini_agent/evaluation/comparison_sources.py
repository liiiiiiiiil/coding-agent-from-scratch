"""Commit resolution, source fingerprints, and isolated comparison checkouts."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
from typing import Iterator

from mini_agent.evaluation.comparison_schema import PINNED_V052_REVISION, sha256_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPARISON_WORKER = Path(__file__).with_name("comparison_worker.py")


def git(*arguments: str, cwd: Path = REPOSITORY_ROOT, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("git source operation failed: " + " ".join(arguments[:2]))
    return result.stdout.decode("utf-8", errors="strict").strip()


def resolve_commit(revision: str) -> str:
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("比较 source_revision 必须是完整 40 位 commit ID")
    resolved = git("rev-parse", "--verify", f"{revision}^{{commit}}")
    if resolved != revision:
        raise ValueError("source_revision 不是规范完整 commit ID")
    return resolved


def current_commit() -> str:
    return git("rev-parse", "HEAD")


def require_clean_worktree() -> None:
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("冻结 v0.53 来源要求工作树干净；请先提交实现和离线验收结果")


def source_tree_fingerprint(source_root: Path) -> str:
    source_root = source_root.resolve(strict=True)
    entries: list[dict[str, str]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        info = path.lstat()
        if not path.is_file() or path.is_symlink():
            raise ValueError("comparison source contains a link or special file")
        entries.append({"path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return sha256_json(entries)


def adapter_fingerprint() -> str:
    info = COMPARISON_WORKER.lstat()
    if not COMPARISON_WORKER.is_file() or COMPARISON_WORKER.is_symlink():
        raise ValueError("comparison worker adapter must be a regular file")
    return hashlib.sha256(COMPARISON_WORKER.read_bytes()).hexdigest()


def environment_snapshot() -> dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
    }


def _safe_extract_archive(revision: str, destination: Path) -> None:
    raw = subprocess.run(
        ["git", "archive", "--format=tar", revision, "src/mini_agent", "pyproject.toml", "tests/fixtures/evaluation/benchmark"],
        cwd=REPOSITORY_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=120, check=False,
    )
    if raw.returncode != 0:
        raise ValueError("git archive could not prepare comparison source")
    def allowed_archive_path(name: str) -> bool:
        pure = PurePosixPath(name)
        if name == "pyproject.toml":
            return True
        if pure.parts in (("src",), ("src", "mini_agent")):
            return True
        if len(pure.parts) >= 2 and pure.parts[:2] == ("src", "mini_agent"):
            return True
        allowed_test_parents = {
            ("tests",), ("tests", "fixtures"),
            ("tests", "fixtures", "evaluation"),
        }
        if pure.parts in allowed_test_parents:
            return True
        return len(pure.parts) >= 4 and pure.parts[:4] == ("tests", "fixtures", "evaluation", "benchmark")

    with tarfile.open(fileobj=io.BytesIO(raw.stdout), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            name = member.name
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or not allowed_archive_path(name):
                raise ValueError("git archive contains an unexpected path")
            if not (member.isfile() or member.isdir()):
                raise ValueError("comparison checkout archive contains a link or special file")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("comparison checkout archive file is unreadable")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            finally:
                source.close()


@contextmanager
def source_checkout(revision: str) -> Iterator[Path]:
    """Materialize only tracked runtime/suite files into an owned temp checkout."""
    resolved = resolve_commit(revision)
    root = Path(tempfile.mkdtemp(prefix="mini-agent-comparison-source-"))
    try:
        _safe_extract_archive(resolved, root)
        yield root
    finally:
        # This path was created by this function and is never caller supplied.
        import shutil
        shutil.rmtree(root)


def source_snapshot(revision: str) -> dict[str, str]:
    """Fingerprint one immutable checkout and verify its declared package version."""
    resolved = resolve_commit(revision)
    with source_checkout(resolved) as root:
        init = (root / "src" / "mini_agent" / "__init__.py").read_text(encoding="utf-8")
        package_version = re.search(r"__version__\s*=\s*[\"']([^\"']+)", init)
        project = (root / "pyproject.toml").read_text(encoding="utf-8")
        project_version = re.search(r"(?m)^version\s*=\s*[\"']([^\"']+)", project)
        if package_version is None or project_version is None or package_version.group(1) != project_version.group(1):
            raise ValueError("source package version metadata does not agree")
        return {
            "revision": resolved,
            "package_version": package_version.group(1),
            "runtime_fingerprint": source_tree_fingerprint(root / "src"),
        }


def preflight_source(checkout: Path) -> dict[str, str]:
    """Confirm an archived target exposes the common Runtime/Memory adapter API."""
    source = checkout / "src"
    if not source.is_dir():
        raise ValueError("target checkout has no src directory")
    home = Path(tempfile.mkdtemp(prefix="mini-agent-comparison-preflight-"))
    try:
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1", "PYTHONPATH": str(source),
        }
        probe = (
            "import inspect; from mini_agent.runtime import AgentRuntime; "
            "from mini_agent.context import ContextManager; from mini_agent.agent import ParentRuntimePolicy; "
            "from mini_agent.memory import MemoryStore; from mini_agent.retrieval import MemoryRetriever; "
            "from mini_agent.evaluation.worker import _build_registry, EvaluationRuntimePolicy; "
            "assert 'token_budget' in inspect.signature(AgentRuntime).parameters; "
            "assert 'memory_retrieval_enabled' in inspect.signature(ContextManager).parameters; "
            "assert callable(getattr(AgentRuntime, 'run', None)); "
            "assert callable(getattr(MemoryStore, 'remember', None)); "
            "assert callable(getattr(MemoryRetriever, 'search_with_total', None)); "
            "assert ParentRuntimePolicy is not None and _build_registry is not None and EvaluationRuntimePolicy is not None"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=checkout, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False,
        )
        if result.returncode != 0:
            raise ValueError("target source is incompatible with comparison Runtime adapter")
        return {
            "preflight": "passed",
            "runtime_fingerprint": source_tree_fingerprint(source),
        }
    finally:
        import shutil
        shutil.rmtree(home)


__all__ = [
    "REPOSITORY_ROOT", "COMPARISON_WORKER", "git", "resolve_commit", "current_commit",
    "require_clean_worktree", "source_tree_fingerprint", "adapter_fingerprint",
    "environment_snapshot", "source_checkout", "source_snapshot", "preflight_source",
    "PINNED_V052_REVISION",
]
