"""Locate the workspace marker used by every repoctl command."""

from pathlib import Path


class WorkspaceNotFound(LookupError):
    pass


def discover_workspace(start: Path | None = None) -> Path:
    location = (Path.cwd() if start is None else Path(start)).resolve()
    marker = location / "workspace.toml"
    if marker.is_file():
        return location
    raise WorkspaceNotFound(f"no workspace.toml at {location}")
