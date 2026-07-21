"""Authoritative planned, authored, and traced seed-ID inventories."""
from __future__ import annotations

import json
from pathlib import Path

from common import (RUNS, STORAGE_ROOT, TRACES, select_seeds,
                    synthetic_tool_contract)
from review_contract import is_accepted


def authored_ids() -> set[str]:
    # Legacy simulator recipes are replacement work, not authored executable
    # seeds. Keeping their files preserves the original work and IDs while the
    # one authoring queue replaces them in place.
    return {seed["id"] for seed in select_seeds()
            if synthetic_tool_contract(seed) is None}


def catalogued_ids() -> set[str]:
    """Return every authored seed record present in the seed catalog."""
    return {seed["id"] for seed in select_seeds()}


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
    ids.update(seed["id"] for seed in select_seeds()
               if synthetic_tool_contract(seed))
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
    for seed in select_seeds():
        if synthetic_tool_contract(seed):
            tags = ", ".join(seed.get("training_tags") or [])
            items[seed["id"]] = (
                "Reauthor this existing objective as a pure Pi-harness seed. "
                "Preserve the ID, capability objective, category, and training "
                f"tags ({tags}). Use only genuine tools installed and executed "
                "by Pi. Do not embed tool results, initial service state, answer "
                "keys, expected call arguments, fictional tool schemas, mock "
                "services, or .invalid URLs. For research, require live web "
                "search and real fetched sources. Original objective: "
                + str(seed.get("prompt") or ""))
    return items


def planned_ids() -> set[str]:
    # Existing catalog entries are documented plans too. Imported planning
    # documents extend the denominator before their artifacts are authored.
    return catalogued_ids() | documented_plan_ids()


def accepted_ids() -> set[str]:
    from import_existing import imported_task_ids
    accepted = set(imported_task_ids())
    for path in (TRACES / "reviews").glob("*.json"):
        try:
            review = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if is_accepted(review):
            accepted.add(path.stem)
    from run_state import connect
    db = connect()
    accepted.update(str(row[0]) for row in db.execute(
        "SELECT DISTINCT seed_id FROM attempts WHERE status='accepted'"))
    db.close()
    return accepted


def trace_state(max_attempts: int) -> dict[str, set[str]]:
    target = catalogued_ids()
    ready = authored_ids()
    needs_reauthoring = target - ready
    accepted = accepted_ids() & ready
    from run_state import connect
    db = connect()
    active = {row[0] for row in db.execute(
        "SELECT DISTINCT j.seed_id FROM jobs j JOIN runs r ON r.id=j.run_id "
        "WHERE r.kind='trace' AND r.status='running' AND j.status='running'")}
    attempts = {row[0]: int(row[1]) for row in db.execute(
        "SELECT seed_id,COUNT(*) FROM attempts "
        "WHERE status IN ('accepted','retry','exhausted') GROUP BY seed_id")}
    db.close()
    active &= ready - accepted
    exhausted = {seed_id for seed_id in ready - accepted - active
                 if attempts.get(seed_id, 0) >= max_attempts}
    waiting = ready - accepted - active - exhausted
    return {"target": target, "accepted": accepted, "active": active,
            "exhausted": exhausted, "waiting": waiting,
            "needs_reauthoring": needs_reauthoring}
