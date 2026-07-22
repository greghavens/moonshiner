"""Authoritative planned, authored, and traced seed-ID inventories."""
from __future__ import annotations

import json
from pathlib import Path

from common import (ROOT, RUNS, STORAGE_ROOT, TRACES, select_seeds,
                    synthetic_tool_contract)
from review_contract import is_accepted


PLANS = ROOT / "plans"


def bundled_plan_records(directory: Path | None = None) -> list[dict]:
    """Expand bundled, data-only authoring plans into unique seed briefs."""
    directory = directory or PLANS
    records: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        plan = json.loads(path.read_text())
        dimensions = plan.get("dimensions") or {}
        domains = dimensions.get("domains") or ["general assistance"]
        constraints = dimensions.get("constraint_sets") or ["follow every instruction"]
        turns = dimensions.get("turn_patterns") or ["a multi-turn exchange"]
        serial = 0
        for family in plan.get("families") or []:
            for offset in range(int(family["count"])):
                serial += 1
                seed_id = f'{plan["id_prefix"]}{serial:04d}'
                brief = family["template"].format(
                    domain=domains[offset % len(domains)],
                    constraint=constraints[(offset // len(domains)) % len(constraints)],
                    turn_pattern=turns[(offset // (len(domains) * len(constraints)))
                                       % len(turns)])
                metadata = (f'Program: {family["program"]}. '
                            f'Category: {family["category"]}. '
                            "Training tags: "
                            + ", ".join(family["training_tags"]) + ". ")
                records.append({"id": seed_id, "brief": brief,
                                "plan": plan["plan"],
                                "scenario": family["scenario"],
                                "program": family["program"],
                                "category": family["category"],
                                "training_tags": list(family["training_tags"]),
                                "artifact_contract": plan["artifact_contract"]})
                records[-1]["brief"] = metadata + records[-1]["brief"]
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("bundled seed plans contain duplicate IDs")
    return records


def bundled_plan_record(seed_id: str) -> dict | None:
    """Return bundled metadata for one planned seed without affecting queue order."""
    return next((record for record in bundled_plan_records()
                 if record["id"] == seed_id), None)


def inventory_sets() -> tuple[set[str], set[str], set[str]]:
    """Load the unified seed catalog once and classify its execution readiness."""
    seeds = select_seeds()
    catalogued = {seed["id"] for seed in seeds}
    replacements = {seed["id"] for seed in seeds
                    if synthetic_tool_contract(seed) is not None}
    return catalogued, catalogued - replacements, replacements


def authored_ids() -> set[str]:
    # Legacy simulator recipes are replacement work, not authored executable
    # seeds. Keeping their files preserves the original work and IDs while the
    # one authoring queue replaces them in place.
    return {seed["id"] for seed in select_seeds()
            if synthetic_tool_contract(seed) is None}


def catalogued_ids() -> set[str]:
    """Return every authored seed record present in the seed catalog."""
    return {seed["id"] for seed in select_seeds()}


def documented_plan_ids(replacement_ids: set[str] | None = None) -> set[str]:
    """Return IDs from every imported documented plan, deduplicated by ID."""
    from author_explicit_waves import catalog_items
    from author_matrix_waves import matrix_items
    ids: set[str] = set()
    ids.update(record["id"] for record in bundled_plan_records())
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
    ids.update(replacement_ids if replacement_ids is not None else
               (seed["id"] for seed in select_seeds()
                if synthetic_tool_contract(seed)))
    return ids


def documented_plan_items() -> dict[str, str]:
    """Return one exact authoring brief per documented, not-yet-cataloged ID."""
    from author_explicit_waves import catalog_items
    from author_matrix_waves import matrix_items
    items: dict[str, str] = {}
    items.update((record["id"], record["brief"])
                 for record in bundled_plan_records())
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


def planned_ids(catalogued: set[str] | None = None,
                replacement_ids: set[str] | None = None) -> set[str]:
    # Existing catalog entries are documented plans too. Imported planning
    # documents extend the denominator before their artifacts are authored.
    return ((catalogued if catalogued is not None else catalogued_ids())
            | documented_plan_ids(replacement_ids))


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


def accepted_ids(db=None, *, include_review_files: bool = True) -> set[str]:
    from import_existing import imported_task_ids
    accepted = set(imported_task_ids())
    if include_review_files:
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
    from run_state import accepted_attempt_versions, pending_trace_queue_entries
    seed_versions = accepted_attempt_versions(db, "seed")
    trace_versions = accepted_attempt_versions(db, "trace")
    queued = {entry["seed_id"] for entry in pending_trace_queue_entries(db)}
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
    accepted.difference_update(queued)
    return accepted


def trace_state(max_attempts: int, *, target: set[str] | None = None,
                ready: set[str] | None = None,
                accepted: set[str] | None = None) -> dict[str, set[str]]:
    target = target if target is not None else catalogued_ids()
    ready = ready if ready is not None else authored_ids()
    needs_reauthoring = target - ready
    accepted = (accepted if accepted is not None else accepted_ids()) & ready
    from run_state import connect, now
    db = connect()
    active = {row[0] for row in db.execute(
        "SELECT DISTINCT j.seed_id FROM jobs j JOIN runs r ON r.id=j.run_id "
        "WHERE r.kind='trace' AND r.status='running' AND j.status='running' "
        "AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at>?", (now(),))}
    from run_state import (trace_attempt_counts_for_current_seed_revision,
                           trace_reasoning_efforts_for_current_seed_revisions)
    attempts = trace_attempt_counts_for_current_seed_revision(db)
    from common import CONFIG
    trace_config = ((CONFIG.get("pipeline") or {}).get("trace") or {})
    stepdown = bool(trace_config.get("step_down_reasoning_on_failure", True))
    remaining = None
    if stepdown:
        from reasoning_stepdown import next_reasoning_stage, reasoning_schedule
        effort = str((CONFIG.get("teacher") or {}).get("reasoning") or "max")
        required = reasoning_schedule(max_attempts, True, effort)
        histories = trace_reasoning_efforts_for_current_seed_revisions(
            db, ready - accepted - active)
        remaining = {
            seed_id for seed_id in ready - accepted - active
            if next_reasoning_stage(
                required, histories.get(seed_id, []))
            is not None
        }
    db.close()
    active &= ready - accepted
    if remaining is None:
        exhausted = {seed_id for seed_id in ready - accepted - active
                     if attempts.get(seed_id, 0) >= max_attempts}
    else:
        exhausted = ready - accepted - active - remaining
    waiting = ready - accepted - active - exhausted
    return {"target": target, "accepted": accepted, "active": active,
            "exhausted": exhausted, "waiting": waiting,
            "needs_reauthoring": needs_reauthoring}
