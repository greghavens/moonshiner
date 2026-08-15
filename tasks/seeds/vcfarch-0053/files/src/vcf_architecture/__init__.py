"""Build machine-readable VCF architecture artifacts from the pinned inputs."""

from pathlib import Path


def build(root: Path, output: Path) -> None:
    """Build the requested architecture artifacts."""
    raise NotImplementedError("architecture builder has not been implemented")

