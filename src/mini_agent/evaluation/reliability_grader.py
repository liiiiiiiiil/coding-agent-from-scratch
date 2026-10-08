"""Independent, bounded behavior oracle for reliability coding tasks."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def grade(workspace: Path, relative: str) -> bool:
    if relative not in {"src/example.py", "src/fallback.py"}:
        raise ValueError("unknown_grader_target")
    root = workspace.resolve(strict=True)
    target = root / relative
    if any(part.is_symlink() for part in (root / "src", target)):
        return False
    if not target.is_file() or target.stat().st_size > 1024 * 1024:
        return False
    spec = importlib.util.spec_from_file_location("reliability_subject", target)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    # Discard subject output without accumulating it in memory or evidence.
    sys.path.insert(0, str(root))
    with open(os.devnull, "w") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            spec.loader.exec_module(module)
            function = getattr(module, "transform", None)
            if not callable(function):
                return False
            return all(function(value) == value * 2 for value in (-17, -2, 0, 1, 3, 100, 1.5))


def main() -> int:
    try:
        passed = len(sys.argv) == 3 and grade(Path(sys.argv[1]), sys.argv[2])
    except BaseException:
        passed = False
    print(json.dumps({"passed": bool(passed)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
