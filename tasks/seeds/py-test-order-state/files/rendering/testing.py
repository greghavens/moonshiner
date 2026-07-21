"""Fixtures for tests that temporarily customize the renderer registry."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from . import registry


@contextmanager
def preserved_registry() -> Iterator[None]:
    """Restore the process-wide registry when the fixture exits."""

    saved = registry._RENDERERS
    try:
        yield
    finally:
        registry._RENDERERS = saved
