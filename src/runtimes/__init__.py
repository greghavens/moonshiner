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
           "NoCompatibleTraceHarness", "TraceHarnessInfrastructureFailure"]


class NoCompatibleTraceHarness(RuntimeError):
    """No installed, configured trace harness provides required capabilities."""


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


def _capability_list(seed: dict, field: str) -> list[str]:
    value = seed.get(field) or []
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value):
        raise TraceHarnessInfrastructureFailure(
            f"{field} must be a list of nonempty strings")
    return list(dict.fromkeys(value))


def _alternative_role(role: dict, name: str, runtime_config: dict) -> dict:
    """The teacher role as the named *alternative* harness has to run it.

    Model identifiers are provider-scoped: one model is ``claude-fable-5`` to
    Claude Code and ``anthropic/claude-fable-5`` to ZenMux. Handing an
    alternative harness the configured teacher's spelling asks it for a model
    its provider has never heard of, so a harness eligible for capability
    selection names its own model in its own runtime block.
    """
    alternative = {**role, "runtime": name}
    for field, override in (("model", "trace_model"),
                            ("reasoning", "trace_reasoning")):
        value = runtime_config.get(override)
        if value:
            alternative[field] = str(value)
    return alternative


def trace_harness_alternatives(config: dict | None = None) -> list[Runtime]:
    """Harnesses ``pipeline.trace.harness_order`` may route a seed to.

    Capability selection deliberately authenticates a harness only after
    choosing it, where a failure is terminal and stops the run. That leaves no
    earlier moment to notice an alternative harness has no credential, so
    ``doctor`` walks this list to raise the alarm before a seed is at stake.
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
            found.append(get_runtime(
                "teacher", {**config, "teacher": {**role, "runtime": name}}))
        except (SystemExit, Exception):
            continue
    return found


def _provided_capabilities(runtime: Runtime) -> frozenset[str]:
    try:
        return frozenset(runtime.trace_capabilities())
    except (AttributeError, TypeError):
        # Preserve compatibility with lightweight Runtime stand-ins used by
        # callers that do not opt into capability-aware selection.
        return frozenset()


def _authenticated_preflight(runtime: Runtime) -> None:
    try:
        runtime.preflight(require_auth=True)
    except (SystemExit, Exception) as error:
        raise TraceHarnessInfrastructureFailure(
            f"selected trace harness {runtime.name!r} is unavailable: {error}") from error


def _resolution(runtime: Runtime, mode: str, required: list[str],
                preferred: list[str], provided: frozenset[str]) -> dict:
    return {
        "mode": mode,
        "runtime": runtime.name,
        "model": runtime.role.get("model"),
        "required": required,
        "preferred": preferred,
        "provided": sorted(provided),
        "matched_preferred": [name for name in preferred if name in provided],
    }


def resolve_trace_harness(seed: dict, configured_teacher: Runtime | None = None,
                          config: dict | None = None) -> tuple[Runtime, dict]:
    """Resolve one installed native harness using only explicit capabilities."""
    config = config or (configured_teacher.config if configured_teacher else CONFIG)
    configured_teacher = configured_teacher or get_teacher(config)
    required = _capability_list(seed, "required_harness_capabilities")
    preferred = _capability_list(seed, "preferred_harness_capabilities")

    if not required and not preferred:
        _authenticated_preflight(configured_teacher)
        provided = _provided_capabilities(configured_teacher)
        return configured_teacher, _resolution(
            configured_teacher, "configured_default", required, preferred, provided)

    configured_runtimes = config.get("runtimes") or {}
    order_value = (((config.get("pipeline") or {}).get("trace") or {})
                   .get("harness_order") or [])
    if (not isinstance(order_value, list)
            or any(not isinstance(name, str) or not name.strip()
                   for name in order_value)):
        raise TraceHarnessInfrastructureFailure(
            "pipeline.trace.harness_order must be a list of nonempty strings")
    order = list(dict.fromkeys(order_value)) or [configured_teacher.name]

    candidates: list[tuple[int, int, Runtime, frozenset[str]]] = []
    required_set = set(required)
    preferred_set = set(preferred)
    for position, name in enumerate(order):
        if name not in configured_runtimes:
            continue
        # The configured teacher already states its own model; only a harness
        # standing in for it needs the per-runtime spelling.
        role = ({**configured_teacher.role} if name == configured_teacher.name
                else _alternative_role(configured_teacher.role, name,
                                       configured_runtimes[name] or {}))
        candidate_config = {**config, "teacher": {**role, "runtime": name}}
        try:
            candidate = get_runtime("teacher", candidate_config)
            candidate.preflight(require_auth=False)
        except (SystemExit, Exception):
            continue
        provided = _provided_capabilities(candidate)
        if not required_set <= provided:
            continue
        candidates.append((len(preferred_set & provided), position,
                           candidate, provided))

    if not candidates:
        required_text = ", ".join(required) or "(none)"
        raise NoCompatibleTraceHarness(
            f"no installed configured trace harness provides all required "
            f"capabilities: {required_text}")

    _, _, selected, provided = min(
        candidates, key=lambda item: (-item[0], item[1]))
    # Once chosen, an authentication/configuration failure is terminal. Never
    # try another harness after selecting the paid-call path.
    _authenticated_preflight(selected)
    return selected, _resolution(
        selected, "capability_match", required, preferred, provided)
