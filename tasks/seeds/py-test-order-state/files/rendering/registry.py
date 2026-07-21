"""Process-wide registry of named renderers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

Renderer = Callable[[Any], str]


def _render_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _render_text(value: Any) -> str:
    return str(value)


_RENDERERS: dict[str, Renderer] = {
    "json": _render_json,
    "text": _render_text,
}


def _normalize_name(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("renderer name must not be empty")
    return normalized


def register(name: str, renderer: Renderer, *, replace: bool = False) -> None:
    """Register a renderer, optionally replacing an existing entry."""

    normalized = _normalize_name(name)
    if not callable(renderer):
        raise TypeError("renderer must be callable")
    if normalized in _RENDERERS and not replace:
        raise ValueError(f"renderer already registered: {normalized}")
    _RENDERERS[normalized] = renderer


def unregister(name: str) -> None:
    """Remove an existing renderer."""

    del _RENDERERS[_normalize_name(name)]


def registered_names() -> tuple[str, ...]:
    """Return registered names in a stable order."""

    return tuple(sorted(_RENDERERS))


def render(name: str, value: Any) -> str:
    """Render a value with the selected renderer."""

    normalized = _normalize_name(name)
    try:
        renderer = _RENDERERS[normalized]
    except KeyError:
        raise KeyError(f"unknown renderer: {normalized}") from None
    return renderer(value)
