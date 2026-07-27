"""Legacy archive path retained for migration tooling, not production packaging."""

from pathlib import Path


def write_archive_member(destination, content):
    destination.write_bytes(content)


def extract_legacy(root, members):
    root = Path(root)
    for member_name, content in members:
        destination = root / member_name
        write_archive_member(destination, content)
