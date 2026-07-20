#!/usr/bin/env python3
"""Create the immutable 1,000-slot Round 2 behavioral curriculum.

The first-round files are never opened for writing.  New IDs continue each
category's numeric sequence, and an existing Round 2 path must be byte-identical
to its deterministic template or expansion fails closed.
"""
from __future__ import annotations

import json
from pathlib import Path

from author_behavior_seeds import CATEGORY_COUNTS, OUT, make_seed

ROUND2_COUNTS = {
    "parallel-same": 90,
    "parallel-mixed": 110,
    "dependency-planning": 150,
    "multi-turn-state": 150,
    "tool-selection": 50,
    "multiple-functions": 50,
    "missing-parameter": 50,
    "missing-function": 25,
    "relevance-abstention": 75,
    "error-recovery": 75,
    "format-sensitivity": 25,
    "long-context-composite": 50,
    "web-research": 50,
    "persistent-memory": 50,
}

# The fixed 400-seed breadth reserve from the roadmap. Remaining slots are
# function-calling benchmark-informed concentration on the known weak behavior families.
BREADTH = {
    "parallel-same": 40,
    "parallel-mixed": 40,
    "dependency-planning": 70,
    "multi-turn-state": 100,
    "error-recovery": 75,
    "missing-parameter": 25,
    "relevance-abstention": 25,
    "format-sensitivity": 25,
}


def main() -> int:
    original = dict(CATEGORY_COUNTS)
    if set(ROUND2_COUNTS) - set(original):
        raise SystemExit("Round 2 contains an unknown behavior category")
    if sum(ROUND2_COUNTS.values()) != 1000 or sum(BREADTH.values()) != 400:
        raise SystemExit("Round 2 allocation drifted")
    OUT.mkdir(parents=True, exist_ok=True)
    created = 0
    for category, addition in ROUND2_COUNTS.items():
        start = original[category]
        breadth = BREADTH.get(category, 0)
        for offset in range(addition):
            seed = make_seed(category, start + offset)
            tags = set(seed["training_tags"])
            tags.add("round:2")
            tags.add("source:breadth-reserve" if offset < breadth
                     else "source:benchmark-informed")
            tags.add(f"weakness:{category}")
            seed["training_tags"] = sorted(tags)
            path = OUT / f"{seed['id']}.json"
            payload = json.dumps(seed, indent=2, sort_keys=True) + "\n"
            if path.exists():
                if path.read_text() != payload:
                    raise SystemExit(f"refusing to replace differing seed: {path}")
                continue
            path.write_text(payload)
            created += 1
    print(f"created {created} new Round 2 behavior seeds; target corpus is 2,000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
