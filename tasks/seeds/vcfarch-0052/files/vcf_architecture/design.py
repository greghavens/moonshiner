"""Architecture builder."""

from __future__ import annotations

from typing import Any


def build_architecture(
    inventory: dict[str, Any], compatibility: dict[str, Any]
) -> dict[str, Any]:
    """Build an installer SddcSpec with its architecture extension."""
    raise NotImplementedError("build the VCF 9.0 architecture")
