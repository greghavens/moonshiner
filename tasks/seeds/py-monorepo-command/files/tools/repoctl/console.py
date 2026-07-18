"""Console-script adapter for repoctl commands."""

import sys

from repoctl.command import member_lines
from repoctl.discovery import WorkspaceNotFound


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["members"]:
        print("usage: repo-members members", file=sys.stderr)
        return 64
    try:
        lines = member_lines()
    except WorkspaceNotFound as exc:
        print(f"repo-members: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0
