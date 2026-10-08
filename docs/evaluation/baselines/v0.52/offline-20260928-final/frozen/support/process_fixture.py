"""Controlled process owned only by the reliability runner."""
from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, choices=(0.8,))
    parser.add_argument("--exit-code", type=int, choices=(0, 7), default=0)
    args = parser.parse_args()
    print("ready", flush=True)
    time.sleep(args.seconds)
    print("finished", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
