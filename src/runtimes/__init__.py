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
           "get_seed_judge", "runtime_names", "source_runtime_names"]


def _build_registry() -> dict[str, type[Runtime]]:
    from runtimes.claude_code import ClaudeCodeRuntime
    from runtimes.codex import CodexRuntime
    from runtimes.pi import PiRuntime
    return {cls.name: cls for cls in (CodexRuntime, ClaudeCodeRuntime, PiRuntime)}


REGISTRY = _build_registry()


def runtime_names() -> list[str]:
    return sorted(REGISTRY)


def source_runtime_names() -> list[str]:
    """Runtimes allowed to author seeds or produce training traces."""
    return sorted(name for name in REGISTRY if name != "claude-code")


def get_runtime(role: str, config: dict | None = None) -> Runtime:
    config = config or CONFIG
    role_config = config[role]
    name = role_config["runtime"]
    try:
        cls = REGISTRY["pi"] if name.startswith("pi-") else REGISTRY[name]
    except KeyError:
        raise SystemExit(
            f"unknown {role} runtime {name!r}; choose from {runtime_names()}") from None
    runtime = cls(config, role_config)
    # Pi provider profiles share one adapter but retain distinct configuration
    # and provenance identities (pi-openrouter, pi-openai, pi-anthropic, ...).
    runtime.name = name
    runtime.runtime_config = config.get("runtimes", {}).get(name, {})
    return runtime


def get_teacher(config: dict | None = None) -> Runtime:
    runtime = get_runtime("teacher", config)
    if runtime.name == "claude-code":
        raise SystemExit("claude-code is judge-only and cannot produce trace sources")
    return runtime


def get_judge(config: dict | None = None) -> Runtime:
    return get_runtime("judge", config)


def get_seed_author(config: dict | None = None) -> Runtime:
    runtime = get_runtime("seed_author", config)
    if runtime.name == "claude-code":
        raise SystemExit("claude-code is judge-only and cannot author seeds")
    return runtime


def get_seed_judge(config: dict | None = None) -> Runtime:
    return get_runtime("seed_judge", config)
