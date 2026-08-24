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
           "get_seed_judge", "runtime_names", "source_runtime_names",
           "resolve_trace_harness", "trace_harness_alternatives",
           "seed_reasoning_is_available",
           "NoCompatibleTraceHarness", "TraceHarnessInfrastructureFailure"]


class NoCompatibleTraceHarness(RuntimeError):
    """No installed, configured trace harness captures reasoning when required."""


class TraceHarnessInfrastructureFailure(RuntimeError):
    """The selected trace harness failed before or during trace execution."""


def _build_registry() -> dict[str, type[Runtime]]:
    from runtimes.claude_code import ClaudeCodeRuntime
    from runtimes.codex import CodexRuntime
    from runtimes.opencode import OpenCodeRuntime
    from runtimes.pi import PiRuntime
    from runtimes.vllm import VLLMRuntime
    return {cls.name: cls for cls in (
        CodexRuntime, ClaudeCodeRuntime, PiRuntime, OpenCodeRuntime,
        VLLMRuntime)}


REGISTRY = _build_registry()


def runtime_names() -> list[str]:
    return sorted(REGISTRY)


def source_runtime_names() -> list[str]:
    """Runtimes allowed to author seeds or produce training traces."""
    return runtime_names()


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
    # Legacy configs may carry pi-openrouter / pi-zai names; all resolve to the
    # same PiRuntime class.  Provider, key, and base URL come from the runtime
    # config block — the name itself has no behavioural effect.
    runtime.name = name
    runtime.runtime_config = config.get("runtimes", {}).get(name, {})
    return runtime


def get_teacher(config: dict | None = None) -> Runtime:
    return get_runtime("teacher", config)


def get_judge(config: dict | None = None) -> Runtime:
    return get_runtime("judge", config)


def get_seed_author(config: dict | None = None) -> Runtime:
    return get_runtime("seed_author", config)


def get_seed_judge(config: dict | None = None) -> Runtime:
    return get_runtime("seed_judge", config)


def _requires_reasoning(seed: dict) -> bool:
    """Return whether this seed requires a trace containing reasoning."""
    if "harness" in seed:
        raise TraceHarnessInfrastructureFailure(
            "seed harness bindings are forbidden; traces use the configured teacher")
    for removed in ("required_harness_capabilities",
                    "preferred_harness_capabilities"):
        if removed in seed:
            raise TraceHarnessInfrastructureFailure(
                f"removed seed field {removed} is forbidden")
    value = seed.get("requires_reasoning", False)
    if not isinstance(value, bool):
        raise TraceHarnessInfrastructureFailure(
            "seed requires_reasoning must be true or false")
    return value


def _configured_harness_order(config: dict) -> list[str]:
    order = (((config.get("pipeline") or {}).get("trace") or {})
             .get("harness_order") or [])
    if (not isinstance(order, list)
            or any(not isinstance(name, str) or not name.strip()
                   for name in order)):
        raise TraceHarnessInfrastructureFailure(
            "pipeline.trace.harness_order must be a list of nonempty strings")
    return list(dict.fromkeys(order))


def _configured_harness_names(config: dict, configured_teacher: Runtime) -> list[str]:
    return list(dict.fromkeys(
        [configured_teacher.name, *_configured_harness_order(config)]))


def seed_reasoning_is_available(seed: dict, config: dict | None = None) -> bool:
    """Whether this queue can satisfy a seed's captured-reasoning requirement."""
    config = config or CONFIG
    if not _requires_reasoning(seed):
        return True
    configured = get_teacher(config)
    runtimes = config.get("runtimes") or {}
    for name in _configured_harness_names(config, configured):
        if name not in runtimes:
            continue
        role = ({**configured.role} if name == configured.name
                else _alternative_role(configured.role, name, runtimes[name] or {}))
        try:
            candidate = get_runtime(
                "teacher", {**config, "teacher": {**role, "runtime": name}})
            candidate.preflight(require_auth=False)
        except (SystemExit, Exception):
            continue
        if _captures_reasoning(candidate):
            return True
    return False


def _alternative_role(role: dict, name: str, runtime_config: dict) -> dict:
    """The teacher role as the named *alternative* harness has to run it.

    Model identifiers are provider-scoped: one model is ``claude-fable-5`` to
    Claude Code and ``anthropic/claude-fable-5`` to ZenMux. Handing an
    alternative harness the configured teacher's spelling asks it for a model
    its provider has never heard of, so a reasoning-capturing alternative names
    its own model in its own runtime block.
    """
    alternative = {**role, "runtime": name}
    for field, override in (("model", "trace_model"),
                            ("reasoning", "trace_reasoning")):
        value = runtime_config.get(override)
        if value:
            alternative[field] = str(value)
    return alternative


def trace_harness_alternatives(config: dict | None = None) -> list[Runtime]:
    """Alternative harnesses that can capture reasoning when a seed requires it.

    Selection deliberately authenticates an alternative only after choosing
    it, where a failure is terminal and stops the run. ``doctor`` walks this
    list to raise the alarm before a seed is at stake.
    """
    config = config or CONFIG
    runtimes = config.get("runtimes") or {}
    order = (((config.get("pipeline") or {}).get("trace") or {})
             .get("harness_order") or [])
    if not isinstance(order, list):
        return []
    try:
        configured = get_teacher(config)
    except (SystemExit, Exception):
        return []
    found: list[Runtime] = []
    for name in dict.fromkeys(order):
        if not isinstance(name, str) or name == configured.name \
                or name not in runtimes:
            continue
        role = _alternative_role(configured.role, name, runtimes[name] or {})
        try:
            candidate = get_runtime(
                "teacher", {**config, "teacher": {**role, "runtime": name}})
        except (SystemExit, Exception):
            continue
        if _captures_reasoning(candidate):
            found.append(candidate)
    return found


def _captures_reasoning(runtime: Runtime) -> bool:
    method = getattr(type(runtime), "captures_reasoning", None)
    if method is None:
        return False
    value = method(runtime)
    if not isinstance(value, bool):
        raise TraceHarnessInfrastructureFailure(
            f"trace harness {runtime.name!r} captures_reasoning must be boolean")
    return value


def _authenticated_preflight(runtime: Runtime) -> None:
    try:
        runtime.preflight(require_auth=True)
    except (SystemExit, Exception) as error:
        raise TraceHarnessInfrastructureFailure(
            f"selected trace harness {runtime.name!r} is unavailable: {error}") from error


def _resolution(runtime: Runtime, mode: str, requires_reasoning: bool,
                captures_reasoning: bool) -> dict:
    return {
        "mode": mode,
        "runtime": runtime.name,
        "model": runtime.role.get("model"),
        "requires_reasoning": requires_reasoning,
        "captures_reasoning": captures_reasoning,
    }


def resolve_trace_harness(seed: dict, configured_teacher: Runtime | None = None,
                          config: dict | None = None) -> tuple[Runtime, dict]:
    """Use the configured teacher unless the seed requires captured reasoning."""
    config = config or (configured_teacher.config if configured_teacher else CONFIG)
    configured_teacher = configured_teacher or get_teacher(config)
    requires_reasoning = _requires_reasoning(seed)

    if not requires_reasoning:
        _authenticated_preflight(configured_teacher)
        return configured_teacher, _resolution(
            configured_teacher, "configured_default", False,
            _captures_reasoning(configured_teacher))

    configured_runtimes = config.get("runtimes") or {}
    for name in _configured_harness_names(config, configured_teacher):
        if name not in configured_runtimes:
            continue
        role = ({**configured_teacher.role} if name == configured_teacher.name
                else _alternative_role(configured_teacher.role, name,
                                       configured_runtimes[name] or {}))
        candidate_config = {**config, "teacher": {**role, "runtime": name}}
        try:
            candidate = get_runtime("teacher", candidate_config)
            candidate.preflight(require_auth=False)
        except (SystemExit, Exception):
            continue
        captures_reasoning = _captures_reasoning(candidate)
        if not captures_reasoning:
            continue
        _authenticated_preflight(candidate)
        return candidate, _resolution(
            candidate, "reasoning_required", True, captures_reasoning)

    raise NoCompatibleTraceHarness(
        "seed requires captured reasoning, but no installed configured trace "
        "harness provides it")
