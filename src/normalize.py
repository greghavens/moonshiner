"""Map a trace's ``trace_format`` to the adapter that can normalize it.

Screening and dataset building both need to turn a raw trace into OpenAI-style
messages without knowing which runtime produced it. The producing runtime is
recorded as ``trace_format`` in each trace's meta; this module routes to the
matching adapter's ``parse_stream`` / ``tool_schemas`` so one build path serves
every teacher.
"""
from __future__ import annotations

from pathlib import Path

from runtimes.claude_code import ClaudeCodeRuntime
from runtimes.codex import CodexRuntime
from runtimes.pi import PiRuntime
from runtimes.behavior import BehaviorRuntime

_RUNTIME_CLASSES = (CodexRuntime, PiRuntime, ClaudeCodeRuntime, BehaviorRuntime)
_BY_FORMAT: dict[str, type] = {}
for _cls in _RUNTIME_CLASSES:
    for _fmt in _cls.trace_formats:
        _BY_FORMAT[_fmt] = _cls


def parser_for(trace_format: str):
    try:
        return _BY_FORMAT[trace_format]
    except KeyError:
        raise ValueError(f"no normalizer for trace_format {trace_format!r}; "
                         f"known: {sorted(_BY_FORMAT)}") from None


def parse_trace(path: Path, trace_format: str,
                workspace: str | None) -> tuple[list[dict], dict]:
    return parser_for(trace_format).parse_stream(Path(path), workspace)


def tool_schemas_for(trace_format: str, messages: list[dict]) -> list[dict]:
    return parser_for(trace_format).tool_schemas(messages)
