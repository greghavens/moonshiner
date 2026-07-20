"""Fail-closed discovery of independently accepted upstream seed IDs."""
from __future__ import annotations

import json
from pathlib import Path


def accepted_seed_ids(marker_dir: Path) -> set[str]:
    """Return IDs attested by immutable chunk markers.

    A missing directory, malformed marker, empty chunk, or duplicate ID across
    chunks is an intake error.  Silently broadening selection would authorize
    unreviewed model work, so callers must fail rather than guess.
    """
    if not marker_dir.is_dir():
        raise ValueError(f"accepted seed marker directory is missing: {marker_dir}")
    paths = sorted(marker_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"accepted seed marker directory is empty: {marker_dir}")
    accepted: set[str] = set()
    for path in paths:
        try:
            marker = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid accepted seed marker {path}: {error}") from error
        ids = marker.get("seed_ids")
        if not isinstance(ids, list) or not ids or not all(
                isinstance(value, str) and value.strip() for value in ids):
            raise ValueError(f"accepted seed marker has invalid seed_ids: {path}")
        for seed_id in ids:
            if seed_id in accepted:
                raise ValueError(f"accepted seed ID appears in multiple markers: {seed_id}")
            accepted.add(seed_id)
    return accepted
