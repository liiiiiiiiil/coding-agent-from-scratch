"""Controlled process owned only by the reliability runner."""
from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seconds", type=float, choices=(0.8,))
    mode.add_argument("--wait-for-stop", action="store_true")
    parser.add_argument("--exit-code", type=int, choices=(0, 7), default=0)
    args = parser.parse_args()
    if args.wait_for_stop:
        # Live trials must remain running across an arbitrary model turn.
        # The runner owns this process and must terminate it within its budget.
        while True:
            time.sleep(0.1)
    print("ready", flush=True)
    time.sleep(args.seconds)
    print("finished", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
