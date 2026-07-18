"""Read the shared workspace and project manifests."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Member:
    path: str
    name: str
    kind: str


def load_members(root: Path) -> list[Member]:
    with (root / "workspace.toml").open("rb") as stream:
        workspace = tomllib.load(stream)
    members: list[Member] = []
    for relative in workspace["workspace"]["members"]:
        with (root / relative / "project.toml").open("rb") as stream:
            project = tomllib.load(stream)["project"]
        members.append(Member(relative, project["name"], project["kind"]))
    return members
