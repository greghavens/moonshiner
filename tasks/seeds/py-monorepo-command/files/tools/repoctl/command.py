"""Command behavior shared by package and console entrypoints."""

from pathlib import Path

from repoctl.config import load_members
from repoctl.discovery import discover_workspace


def member_lines(start: Path | None = None) -> list[str]:
    root = discover_workspace(start)
    return [f"{member.name}\t{member.kind}\t{member.path}" for member in load_members(root)]
