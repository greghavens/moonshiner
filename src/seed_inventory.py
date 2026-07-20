"""Authoritative planned, authored, and traced seed-ID inventories."""
from __future__ import annotations

import json
from pathlib import Path

from common import (RUNS, STORAGE_ROOT, TRACES, deterministic_review_accepted,
                    select_seeds)


def authored_ids() -> set[str]:
    return {seed["id"] for seed in select_seeds(require_authored=False)}


def documented_plan_ids() -> set[str]:
    """Return IDs from every imported documented plan, deduplicated by ID."""
    from author_explicit_waves import catalog_items
    from author_matrix_waves import matrix_items
    ids: set[str] = set()
    imports = STORAGE_ROOT / "imports"
    if imports.is_dir():
        for path in sorted(imports.glob("**/WAVE*_USECASES.md")):
            try:
                wave = int(path.name.removeprefix("WAVE").split("_", 1)[0])
                if wave not in {10, 11, 14, 17, 18}:
                    continue
                if wave in {17, 18}:
                    ids.update(seed_id for seed_id, _, _ in matrix_items(path, wave))
                else:
                    ids.update(seed_id for seed_id, _, _ in catalog_items(path))
            except (OSError, ValueError):
                continue
    return ids


def documented_plan_items() -> dict[str, str]:
    """Return one exact authoring brief per documented, not-yet-cataloged ID."""
    from author_explicit_waves import catalog_items
    from author_matrix_waves import matrix_items
    items: dict[str, str] = {}
    imports = STORAGE_ROOT / "imports"
    if not imports.is_dir():
        return items
    for path in sorted(imports.glob("**/WAVE*_USECASES.md")):
        try:
            wave = int(path.name.removeprefix("WAVE").split("_", 1)[0])
            if wave not in {10, 11, 14, 17, 18}:
                continue
            if wave in {17, 18}:
                for seed_id, chunk, objective in matrix_items(path, wave):
                    items.setdefault(seed_id, f"Wave {wave}, chunk {chunk}. {objective}")
            else:
                for seed_id, chunk, text in catalog_items(path):
                    items.setdefault(seed_id, f"Wave {wave}, chunk {chunk}. {text}")
        except (OSError, ValueError):
            continue
    return items


def planned_ids() -> set[str]:
    # Existing catalog entries are documented plans too. Imported planning
    # documents extend the denominator before their artifacts are authored.
    return authored_ids() | documented_plan_ids()


def accepted_ids() -> set[str]:
    from import_existing import imported_task_ids
    accepted = set(imported_task_ids())
    for path in (TRACES / "reviews").glob("*.json"):
        try:
            review = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (review.get("accepted") is True
                and deterministic_review_accepted(review)
                and (review.get("judge") or {}).get("model_attested") is True):
            accepted.add(path.stem)
    return accepted


def trace_state(max_attempts: int) -> dict[str, set[str]]:
    target = authored_ids()
    accepted = accepted_ids() & target
    from run_state import connect
    db = connect()
    active = {row[0] for row in db.execute(
        "SELECT DISTINCT j.seed_id FROM jobs j JOIN runs r ON r.id=j.run_id "
        "WHERE r.kind='trace' AND r.status='running' AND j.status='running'")}
    attempts = {row[0]: int(row[1]) for row in db.execute(
        "SELECT seed_id,COUNT(*) FROM attempts "
        "WHERE status IN ('accepted','retry','exhausted') GROUP BY seed_id")}
    db.close()
    active &= target - accepted
    exhausted = {seed_id for seed_id in target - accepted - active
                 if attempts.get(seed_id, 0) >= max_attempts}
    waiting = target - accepted - active - exhausted
    return {"target": target, "accepted": accepted, "active": active,
            "exhausted": exhausted, "waiting": waiting}
