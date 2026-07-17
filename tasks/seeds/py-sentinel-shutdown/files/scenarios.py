"""Deterministic harness scenarios for RelayPool, one per command-line name.

Each scenario builds a pool with a scripted handler, runs a fixed job list,
and prints a JSON snapshot. The test runner executes every scenario in a
separate process under a hard timeout, so a wedged coordinator cannot hang
the test run itself.
"""

import json
import sys

from relaypool import RelayPool


def handler(payload):
    if isinstance(payload, str) and payload.startswith("boom"):
        raise RuntimeError(f"jam: {payload}")
    return str(payload).upper()


def snapshot(pool, outcomes):
    return {
        "outcomes": sorted([list(o) for o in outcomes]),
        "done_signals": pool.done_signals,
        "workers": pool.workers,
        "alive": pool.alive_workers(),
        "leftover": pool.leftover_messages(),
    }


SCENARIOS = {
    "clean": (2, [("j1", "a"), ("j2", "b"), ("j3", "c"), ("j4", "d"), ("j5", "e")]),
    "one-failure": (2, [("j1", "a"), ("j2", "boom-2"), ("j3", "c"), ("j4", "d")]),
    "failure-first": (3, [("j1", "boom-1"), ("j2", "b"), ("j3", "c")]),
    "single-worker": (1, [("j1", "boom-1"), ("j2", "after")]),
    "all-failures": (2, [("j1", "boom-1"), ("j2", "boom-2"), ("j3", "boom-3")]),
    "no-jobs": (3, []),
}


def main():
    name = sys.argv[1]
    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario: {name}")
    workers, jobs = SCENARIOS[name]
    pool = RelayPool(handler, workers=workers)
    outcomes = pool.run(jobs)
    print(json.dumps(snapshot(pool, outcomes)))


if __name__ == "__main__":
    main()
