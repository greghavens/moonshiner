"""Runtime-agnostic interfaces for teacher trace generation and judging.

A *runtime* is one agentic CLI (Claude Code, Codex, or Pi/GLM) that moonshiner
can drive either as the **teacher** (generating a coding trace to distill) or as
the **judge** (independently reviewing a candidate trace read-only). Each concrete
adapter in this package implements :class:`Runtime`; the pipeline selects one by
name from ``config.json`` so a full distill can be run against any model.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TraceResult:
    """Everything the pipeline needs after a teacher runs on one seed.

    ``raw_path`` is the primary buildable artifact (a persisted rollout or the
    finalized event stream) that ``build_dataset`` later normalizes. Attestation
    fields let the caller decide acceptance without knowing runtime internals.
    """
    raw_path: Path
    trace_format: str
    return_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    stream_success: bool = False
    observed_model: str | None = None
    observed_models: list[str] = field(default_factory=list)
    model_attested: bool = True
    model_fallback: bool = False
    safeguard_refusal: bool = False
    usage: dict = field(default_factory=dict)
    error: str | None = None
    # Set (to a human-readable reason) when a metered account hit a usage limit
    # before the attempt could complete; the caller must fail closed and defer.
    unavailable: str | None = None
    # Runtime-specific provenance merged into the trace's ``teacher`` meta block
    # (e.g. thread_id, session_id, upstream_audit, runtime_version).
    provenance: dict = field(default_factory=dict)


@dataclass
class ReviewResult:
    """Outcome of an independent read-only judge pass over a candidate trace."""
    raw_text: str
    verdict: dict | None
    return_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    observed_model: str | None = None
    model_attested: bool = True
    error: str | None = None


class Runtime(abc.ABC):
    """One agentic CLI usable as teacher and/or judge."""

    #: Stable identifier used in config.json (``teacher.runtime`` / ``judge.runtime``).
    name: str = "base"
    #: ``trace_format`` strings this runtime's normalizer can parse.
    trace_formats: tuple[str, ...] = ()

    def __init__(self, config: dict, role_config: dict):
        self.config = config
        self.role = role_config
        self.runtime_config = config.get("runtimes", {}).get(self.name, {})

    def model_matches(self, observed: str | None) -> bool:
        from model_profile import matches
        profile = self.config.get("model_profile") or {}
        aliases = ((profile.get("attestation_aliases") or [])
                   if profile.get("id") == self.role.get("model") else [])
        return matches(str(self.role.get("model") or ""), observed, aliases)

    @staticmethod
    def require_persistent_workspace(workspace: Path) -> Path:
        """Refuse to launch any model inside an ephemeral temp directory."""
        resolved = Path(workspace).resolve()
        for temporary_root in (Path("/tmp"), Path("/var/tmp")):
            root = temporary_root.resolve()
            if resolved == root or root in resolved.parents:
                raise RuntimeError(
                    f"model workspace must be persistent; temporary path prohibited: {resolved}")
        return resolved

    # -- lifecycle ---------------------------------------------------------- #
    @abc.abstractmethod
    def preflight(self, *, require_auth: bool = False) -> None:
        """Verify the CLI/toolchain is present and correctly pinned.

        Raises ``SystemExit`` with a human message when unusable.
        """

    # -- teacher ------------------------------------------------------------ #
    @abc.abstractmethod
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        """Run the teacher on ``seed`` inside the prepared ``workspace``.

        Implementations write raw artifacts under ``out_dir`` and return a
        :class:`TraceResult`. They must not run verification (the caller does).
        """

    # -- judge -------------------------------------------------------------- #
    @abc.abstractmethod
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        """Run this runtime as an independent, read-only reviewer/judge."""

    # -- normalization (build_dataset) ------------------------------------- #
    @staticmethod
    @abc.abstractmethod
    def parse_stream(path: Path, workspace: str | None
                     ) -> tuple[list[dict], dict]:
        """Convert a raw trace into OpenAI-style messages and parse stats."""

    @staticmethod
    @abc.abstractmethod
    def tool_schemas(messages: list[dict]) -> list[dict]:
        """Declare OpenAI-style schemas for the full tool surface of a trace.

        Returns the complete action space the teacher was offered — the
        adapter's ``OFFERED_TOOLS`` — unioned with any additional tool actually
        observed in ``messages``. A row's ``tools`` therefore always lists what
        the teacher *could* have called, not only what this trace happened to
        call, so the exported dataset preserves the true action space.
        """
