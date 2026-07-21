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
                "Reauthor this existing objective for the selected unmodified agent harness. "
                "Preserve the ID, capability objective, category, and training "
                f"tags ({tags}). Use only genuine tools installed and executed "
                "by that harness. Do not embed tool results, initial service state, answer "
                "keys, expected call arguments, fictional tool schemas, mock "
                "services, or .invalid URLs. For research, require live web "
                "search and real fetched sources. Original objective: "
                + str(seed.get("prompt") or ""))
    return items


def planned_ids() -> set[str]:
    # Existing catalog entries are documented plans too. Imported planning
    # documents extend the denominator before their artifacts are authored.
    return catalogued_ids() | documented_plan_ids()


def retired_seed_ids(db=None) -> set[str]:
    """Return seed-authoring work retired after its own attempt allowance."""
    owns_db = db is None
    if owns_db:
        from run_state import connect
        db = connect()
    retired = {str(row[0]) for row in db.execute("""
        SELECT latest.seed_id FROM (
          SELECT a.seed_id,a.status,
                 ROW_NUMBER() OVER (PARTITION BY a.seed_id ORDER BY a.id DESC) AS rank
          FROM attempts a JOIN runs r ON r.id=a.run_id WHERE r.kind='seed'
        ) AS latest
        WHERE latest.rank=1 AND latest.status IN ('retired','exhausted')""")}
    if owns_db:
        db.close()
    return retired


def accepted_ids(db=None) -> set[str]:
    from import_existing import imported_task_ids
    accepted = set(imported_task_ids())
    for path in (TRACES / "reviews").glob("*.json"):
        try:
            review = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if is_accepted(review):
            accepted.add(path.stem)
    owns_db = db is None
    if owns_db:
        from run_state import connect
        db = connect()
    from run_state import accepted_attempt_versions
    seed_versions = accepted_attempt_versions(db, "seed")
    trace_versions = accepted_attempt_versions(db, "trace")
    if owns_db:
        db.close()
    # Imported rows and filesystem reviews are baseline acceptances. Once the
    # seed author accepts a newer revision, only a subsequently accepted trace
    # can complete that seed again.
    for seed_id, seed_version in seed_versions.items():
        if trace_versions.get(seed_id, 0) <= seed_version:
            accepted.discard(seed_id)
    accepted.update(seed_id for seed_id, trace_version in trace_versions.items()
                    if trace_version > seed_versions.get(seed_id, 0))
    return accepted


def trace_state(max_attempts: int) -> dict[str, set[str]]:
    target = catalogued_ids()
    ready = authored_ids()
    needs_reauthoring = target - ready
    accepted = accepted_ids() & ready
    from run_state import connect, now
    db = connect()
    active = {row[0] for row in db.execute(
        "SELECT DISTINCT j.seed_id FROM jobs j JOIN runs r ON r.id=j.run_id "
        "WHERE r.kind='trace' AND r.status='running' AND j.status='running' "
        "AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at>?", (now(),))}
    from run_state import trace_attempt_counts_for_current_seed_revision
    attempts = trace_attempt_counts_for_current_seed_revision(db)
    db.close()
    active &= ready - accepted
    exhausted = {seed_id for seed_id in ready - accepted - active
                 if attempts.get(seed_id, 0) >= max_attempts}
    waiting = ready - accepted - active - exhausted
    return {"target": target, "accepted": accepted, "active": active,
            "exhausted": exhausted, "waiting": waiting,
            "needs_reauthoring": needs_reauthoring}
