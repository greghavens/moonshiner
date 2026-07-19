"""Rebuild the search index under one database transaction."""

from framework51 import force_text


def rebuild_index(database, labels: list[object], *, dry_run: bool = False) -> list[str]:
    normalized = [force_text(label).strip() for label in labels]
    if dry_run:
        return normalized
    with database.atomic():
        for label in normalized:
            database.replace(label)
    return normalized

