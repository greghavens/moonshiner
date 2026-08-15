"""Migration plan builder."""

from __future__ import annotations

from typing import Any


def build_plan(
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    installer_spec: dict[str, Any],
) -> dict[str, Any]:
    """Return the architecture artifact described by the protected inputs."""
    raise NotImplementedError("VCF migration planning is not implemented")
