"""Runtime registry: select a teacher or judge adapter from config.

``config.teacher.runtime`` and ``config.judge.runtime`` name one of the
registered adapters. Both roles can use the same or different runtimes and
models, which is what makes a full distill configurable end to end — e.g. a
Codex teacher judged by a Claude reviewer, or a Pi teacher judged by Codex.
"""
from __future__ import annotations

from common import CONFIG
from runtimes.base import ReviewResult, Runtime, TraceResult

__all__ = ["Runtime", "TraceResult", "ReviewResult", "REGISTRY",
           "get_runtime", "get_teacher", "get_judge", "get_seed_author",
           "get_seed_judge", "runtime_names"]


def _build_registry() -> dict[str, type[Runtime]]:
    from runtimes.claude_code import ClaudeCodeRuntime
    from runtimes.codex import CodexRuntime
    from runtimes.pi import PiRuntime
    return {cls.name: cls for cls in (CodexRuntime, ClaudeCodeRuntime, PiRuntime)}


REGISTRY = _build_registry()


def runtime_names() -> list[str]:
    return sorted(REGISTRY)


def get_runtime(role: str, config: dict | None = None) -> Runtime:
    config = config or CONFIG
    role_config = config[role]
    name = role_config["runtime"]
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise SystemExit(
            f"unknown {role} runtime {name!r}; choose from {runtime_names()}") from None
    return cls(config, role_config)


def get_teacher(config: dict | None = None) -> Runtime:
    return get_runtime("teacher", config)


def get_judge(config: dict | None = None) -> Runtime:
    return get_runtime("judge", config)


def get_seed_author(config: dict | None = None) -> Runtime:
    return get_runtime("seed_author", config)


def get_seed_judge(config: dict | None = None) -> Runtime:
    return get_runtime("seed_judge", config)
