"""Reclassify confirmed infrastructure failures after their cause is repaired.

An infrastructure failure is not the seed's fault, so its attempts must not
count against the seed's budget. But nothing may be requeued until whatever
broke is demonstrably working again, or the queue simply walks the backlog
back into the same wall. So every blocked seed is sorted into a class by the
error it recorded, and each class carries its own proof of repair:

  missing-tool          the executable now runs inside the sandbox
  provider-credit       the provider reports a balance left to spend
  harness               the runtime passes its own preflight, auth included
  judge-verdict         the judge runtime passes its preflight
  workspace-permission  the unreadable workspace is gone
  content-filter        never repairable, never requeued

The classes are the point. This command used to recognise a missing executable
and nothing else, and to look for it only in attempts marked ``retry``,
``exhausted`` or ``failed`` -- but an infrastructure failure is recorded as
``infrastructure_error``, so no row it scanned could ever hold one. It reported
"0 confirmed infrastructure failures" while 18 seeds sat blocked behind
exhausted provider credit, two unreadable workspaces and a flaked judge.

Verification, not repair, with one exception: a workspace that cannot be read
is cleared here under ``--yes``, through the guarded remover. Nothing else
knows the workspace is dead -- pruning deliberately preserves a blocked seed's
workspace for audit, which would leave the seed blocked on evidence that only
this command is finished with.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

from common import STORAGE_ROOT, _sandboxed_command
from run_state import connect, now
from toolchains import missing_executables

CONTENT_FILTER = ("contentfiltered", "content filter", "content_filter",
                  "safeguard_refusal")
PROVIDER_CREDIT = ("more credits", "payment required", "insufficient credit",
                   "insufficient_quota", "'statuscode': 402", '"code":402')
JUDGE_VERDICT = ("judge verdict malformed", "judge review is missing")
HARNESS = ("traceharnessinfrastructurefailure", "modelunavailable",
           "nocompatibletraceharness", "not logged in")
QUOTED_PATH = re.compile(r"['\"](/[^'\"]+)['\"]")


def sandbox_tool_ready(tool: str) -> tuple[bool, str]:
    workspace = STORAGE_ROOT / "environment-check"
    workspace.mkdir(parents=True, exist_ok=True)
    version_args = {"go": ["version"], "java": ["--version"],
                    "pwsh": ["--version"]}.get(tool, ["--version"])
    result = _sandboxed_command([tool, *version_args], workspace, 60)
    detail = (result.stdout + "\n" + result.stderr).strip()[-1000:]
    return result.returncode != 127 and not missing_executables(detail), detail


def runtime_ready(role: str) -> tuple[bool, str]:
    """Whether the runtime configured for ``role`` can run. No model call."""
    from runtimes import get_runtime
    try:
        get_runtime(role).preflight(require_auth=True)
    except SystemExit as error:
        return False, f"{role}: {error}"
    except Exception as error:                       # noqa: BLE001
        return False, f"{role}: {type(error).__name__}: {error}"
    return True, f"{role}: preflight passed"


def provider_credit_ready(role: str) -> tuple[bool, str]:
    """Whether the role's provider still reports money to spend.

    Fails closed. A provider that cannot be asked has not proven anything, and
    requeueing into an empty account re-blocks every seed it touches at the
    price of a full prompt each time -- which is how this class of failure
    stopped an 8-hour round in the first place.
    """
    from configuration import load_config
    from runtimes.auth import load_provider_key
    config = load_config()
    name = str((config.get(role) or {}).get("runtime") or "").strip()
    runtime_config = (config.get("runtimes") or {}).get(name) or {}
    base_url = str(runtime_config.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return False, f"{role}: {name or 'runtime'} declares no base_url to ask"
    try:
        key = load_provider_key(runtime_config)
    except Exception as error:                       # noqa: BLE001
        return False, f"{role}: {error}"
    request = urllib.request.Request(
        f"{base_url}/credits", headers={"Authorization": f"Bearer {key}",
                                        "User-Agent": "moonshiner"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as error:                       # noqa: BLE001
        return False, f"{role}: {name} reports no balance ({error})"
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else payload
    total, used = data.get("total_credits"), data.get("total_usage")
    if not isinstance(total, (int, float)) or not isinstance(used, (int, float)):
        return False, f"{role}: {name} balance response carries no credit total"
    remaining = float(total) - float(used)
    return remaining > 0, f"{role}: {remaining:.2f} left with {name}"


def workspace_root(text: str) -> Path | None:
    """The workspace directory a permission error names, if it names one."""
    from common import WORKSPACES
    try:
        root = WORKSPACES.resolve()
    except OSError:
        return None
    for value in QUOTED_PATH.findall(text):
        candidate = Path(value)
        try:
            relative = candidate.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if relative.parts:
            return root / relative.parts[0]
    return None


def workspace_cleared(text: str, *, apply: bool) -> tuple[bool, str]:
    target = workspace_root(text)
    if target is None:
        return False, "permission error names no workspace"
    if not target.exists():
        return True, f"already cleared: {target.name}"
    if not apply:
        return True, f"would clear the unreadable workspace: {target.name}"
    from common import WORKSPACES, remove_workspace
    try:
        remove_workspace(target, workspaces=WORKSPACES)
    except (OSError, ValueError) as error:
        return False, f"cannot clear {target.name}: {error}"
    return True, f"cleared the unreadable workspace: {target.name}"


def classify(text: str) -> tuple[str, str]:
    """Sort one failure into a repair class and the key its proof is cached by.

    Order decides overlaps: an exhausted balance arrives wrapped in the same
    ``ModelUnavailable`` a broken harness raises, and a provider block arrives
    wrapped in the harness failure the adapter raised around it. The narrower
    reading wins, and the filter wins over everything.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in CONTENT_FILTER):
        return "content-filter", "content-filter"
    tools = missing_executables(text)
    if tools:
        return "missing-tool", ",".join(tools)
    if any(marker in lowered for marker in PROVIDER_CREDIT):
        return "provider-credit", "provider-credit"
    if "permissionerror" in lowered:
        # Keyed by the workspace itself: two seeds fail this way independently
        # and clearing one proves nothing about the other.
        target = workspace_root(text)
        return "workspace-permission", f"workspace:{target.name}" if target \
            else "workspace-permission"
    if any(marker in lowered for marker in JUDGE_VERDICT):
        return "judge-verdict", "judge-verdict"
    if any(marker in lowered for marker in HARNESS):
        return "harness", "harness"
    return "", ""


def probe(name: str, key: str, text: str, *, apply: bool) -> tuple[bool, str]:
    if name == "missing-tool":
        results = [sandbox_tool_ready(tool) for tool in key.split(",")]
        return (all(ready for ready, _ in results),
                " | ".join(detail for _, detail in results)[-500:])
    if name == "provider-credit":
        return provider_credit_ready("teacher")
    if name == "workspace-permission":
        return workspace_cleared(text, apply=apply)
    if name == "judge-verdict":
        return runtime_ready("judge")
    if name == "harness":
        return runtime_ready("teacher")
    return False, f"no proof of repair defined for {name}"


def repair(db, *, apply: bool) -> dict:
    checks: dict[str, tuple[bool, str]] = {}
    attempts: list[int] = []
    seeds: set[str] = set()
    refused: set[str] = set()
    # Legacy rows are read wherever they sit; an `infrastructure_error` row is
    # read only while its seed is still blocked, so a repaired seed is not
    # re-reported on every later run.
    rows = db.execute(
        "SELECT a.id,a.seed_id,a.review_json,a.error,j.last_error "
        "FROM attempts a JOIN runs r ON r.id=a.run_id "
        "LEFT JOIN jobs j ON j.run_id=a.run_id AND j.seed_id=a.seed_id "
        "WHERE r.kind='trace' AND (a.status IN ('retry','exhausted','failed') "
        "OR (a.status='infrastructure_error' "
        "    AND j.status='infrastructure_blocked'))").fetchall()
    for row in rows:
        try:
            review = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            review = {}
        text = " | ".join([
            *((review.get("deterministic") or {}).get("failures") or []),
            str(review.get("reason") or ""), str(row[3] or ""),
            str(row[4] or ""),
        ])
        name, key = classify(text)
        if not name:
            continue
        if name == "content-filter":
            # A refusal is a property of the prompt, not of the environment.
            # Re-running one buys another refusal at the price of a full
            # prompt, so no proof of repair exists and none is looked for.
            refused.add(str(row[1]))
            continue
        if key not in checks:
            checks[key] = probe(name, key, text, apply=apply)
        if checks[key][0]:
            attempts.append(int(row[0]))
            seeds.add(str(row[1]))
    if apply and attempts:
        placeholders = ",".join("?" for _ in attempts)
        db.execute(
            f"UPDATE attempts SET status='infrastructure_error',finished_at=?,"
            f"error=COALESCE(error,'confirmed infrastructure failure') "
            f"WHERE id IN ({placeholders})", (now(), *attempts))
        seed_placeholders = ",".join("?" for _ in seeds)
        db.execute(
            f"UPDATE jobs SET status='retry',last_error=NULL,updated_at=? "
            f"WHERE status='infrastructure_blocked' "
            f"AND seed_id IN ({seed_placeholders})", (now(), *sorted(seeds)))
        db.commit()
    candidate_counts: dict[str, int] = {}
    for attempt_id in attempts:
        seed_id = str(db.execute("SELECT seed_id FROM attempts WHERE id=?",
                                 (attempt_id,)).fetchone()[0])
        candidate_counts[seed_id] = candidate_counts.get(seed_id, 0) + 1
    accepted = {str(row[0]) for row in db.execute(
        "SELECT DISTINCT a.seed_id FROM attempts a JOIN runs r ON r.id=a.run_id "
        "WHERE r.kind='trace' AND a.status='accepted'")}
    valid_counts = {str(row[0]): int(row[1]) for row in db.execute(
        "SELECT a.seed_id,COUNT(*) FROM attempts a JOIN runs r ON r.id=a.run_id "
        "WHERE r.kind='trace' AND a.status IN ('accepted','retry','exhausted') "
        "GROUP BY a.seed_id")}
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
            "requeued_ids": sorted(eligible), "checks": checks,
            "refused": sorted(refused),
            "tools": {key: ready for key, (ready, _) in checks.items()}}


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
    for key, (ready, detail) in sorted(result["checks"].items()):
        print(f"  {'ready' if ready else 'BLOCKED'}: {key} — {detail}")
    if result["refused"]:
        print(f"  refused: {len(result['refused'])} content-filtered seed(s), "
              f"which a rerun can only refuse again: "
              + ", ".join(result["refused"]))
    if not args.yes:
        print("Run `moonshiner trace repair-infrastructure --yes` after reviewing this result.")
    return 0 if all(result["tools"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
