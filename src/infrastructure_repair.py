"""Reclassify confirmed infrastructure failures after their cause is repaired."""
from __future__ import annotations

import argparse
import json

from common import STORAGE_ROOT, _sandboxed_command
from run_state import connect, now
from toolchains import missing_executables


def sandbox_tool_ready(tool: str) -> tuple[bool, str]:
    workspace = STORAGE_ROOT / "environment-check"
    workspace.mkdir(parents=True, exist_ok=True)
    version_args = {"go": ["version"], "java": ["--version"],
                    "pwsh": ["--version"]}.get(tool, ["--version"])
    result = _sandboxed_command([tool, *version_args], workspace, 60)
    detail = (result.stdout + "\n" + result.stderr).strip()[-1000:]
    return result.returncode != 127 and not missing_executables(detail), detail


def repair(db, *, apply: bool) -> dict:
    repaired_tools: dict[str, bool] = {}
    attempts: list[int] = []
    seeds: set[str] = set()
    rows = db.execute(
        "SELECT id,seed_id,review_json,error FROM attempts "
        "WHERE status IN ('retry','exhausted','failed')").fetchall()
    for row in rows:
        try:
            review = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            review = {}
        text = " | ".join([
            *((review.get("deterministic") or {}).get("failures") or []),
            str(review.get("reason") or ""), str(row[3] or ""),
        ])
        tools = missing_executables(text)
        if not tools:
            continue
        ready = True
        for tool in tools:
            if tool not in repaired_tools:
                repaired_tools[tool] = sandbox_tool_ready(tool)[0]
            ready = ready and repaired_tools[tool]
        if ready:
            attempts.append(int(row[0])); seeds.add(str(row[1]))
    if apply and attempts:
        placeholders = ",".join("?" for _ in attempts)
        db.execute(
            f"UPDATE attempts SET status='infrastructure_error',finished_at=?,"
            f"error=COALESCE(error,'confirmed infrastructure failure') "
            f"WHERE id IN ({placeholders})", (now(), *attempts))
        db.commit()
    candidate_counts: dict[str, int] = {}
    for attempt_id in attempts:
        seed_id = str(db.execute("SELECT seed_id FROM attempts WHERE id=?",
                                 (attempt_id,)).fetchone()[0])
        candidate_counts[seed_id] = candidate_counts.get(seed_id, 0) + 1
    accepted = {str(row[0]) for row in db.execute(
        "SELECT DISTINCT seed_id FROM attempts WHERE status='accepted'")}
    valid_counts = {str(row[0]): int(row[1]) for row in db.execute(
        "SELECT seed_id,COUNT(*) FROM attempts "
        "WHERE status IN ('accepted','retry','exhausted') GROUP BY seed_id")}
    from configuration import load_config
    maximum = int((((load_config().get("pipeline") or {}).get("trace") or {})
                   .get("max_attempts", 2)))
    eligible = []
    for seed_id in seeds - accepted:
        remaining = valid_counts.get(seed_id, 0)
        if not apply:
            remaining -= candidate_counts.get(seed_id, 0)
        if remaining < maximum:
            eligible.append(seed_id)
    return {"attempts": len(attempts), "seeds": len(seeds),
            "seed_ids": sorted(seeds), "requeued": len(eligible),
            "requeued_ids": sorted(eligible), "tools": repaired_tools}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner trace repair-infrastructure")
    parser.add_argument("--yes", action="store_true",
                        help="Apply the verified reclassification and requeue.")
    args = parser.parse_args(argv)
    db = connect()
    try:
        result = repair(db, apply=args.yes)
    finally:
        db.close()
    mode = "reclassified" if args.yes else "would reclassify"
    print(f"{mode} {result['attempts']} confirmed infrastructure-failed attempts "
          f"across {result['seeds']} seeds; {result['requeued']} seeds "
          f"{'are now' if args.yes else 'would become'} trace-eligible")
    for tool, ready in sorted(result["tools"].items()):
        print(f"  {'ready' if ready else 'BLOCKED'}: {tool}")
    if not args.yes:
        print("Run `moonshiner trace repair-infrastructure --yes` after reviewing this result.")
    return 0 if all(result["tools"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
