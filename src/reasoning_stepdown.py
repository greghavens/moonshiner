"""Config-driven reasoning-effort scheduling for ordinary trace attempts."""
from __future__ import annotations

import copy
from collections import Counter

CANONICAL_CYCLE = ("xhigh", "medium", "low")


def canonical_stage(value: str | None) -> str | None:
    value = str(value or "").strip().lower()
    if value in {"xhigh", "max"}:
        return "xhigh"
    if value in {"medium", "low"}:
        return value
    return value or None


def reasoning_schedule(max_attempts: int, enabled: bool,
                       configured_effort: str) -> list[str]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if not enabled:
        return [configured_effort] * max_attempts
    return [CANONICAL_CYCLE[index % len(CANONICAL_CYCLE)]
            for index in range(max_attempts)]


def next_reasoning_stage(required: list[str], completed: list[str]) -> str | None:
    """Return the first required occurrence not covered by completed efforts."""
    available = Counter(canonical_stage(value) for value in completed)
    needed = Counter()
    for stage in required:
        canonical = canonical_stage(stage)
        needed[canonical] += 1
        if available[canonical] < needed[canonical]:
            return stage
    return None


def native_effort(runtime_name: str, stage: str) -> str:
    canonical = canonical_stage(stage) or stage
    if runtime_name.startswith("pi") and canonical == "xhigh":
        return "max"
    return canonical


def runtime_for_stage(runtime, stage: str):
    """Clone one worker runtime with an attempt-local reasoning effort."""
    if runtime.name == "claude-code":
        raise ValueError(
            "claude-code does not expose a documented reasoning-effort CLI flag; "
            "disable pipeline.trace.step_down_reasoning_on_failure")
    adjusted = copy.copy(runtime)
    adjusted.role = dict(runtime.role)
    adjusted.role["reasoning"] = native_effort(runtime.name, stage)
    return adjusted
