"""Human-facing authentication and environment diagnostics."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys

from common import (CONFIG, SEEDS_DIR, STORAGE_ROOT, key_env_name, key_file_path,
                    key_persist_path)
from runtimes import get_judge, get_teacher


def auth_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner.py auth")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("set", "status", "remove"):
        child = sub.add_parser(action)
        child.add_argument("runtime", help="Configured runtime name, e.g. pi")
    args = parser.parse_args(argv)
    runtime = (CONFIG.get("runtimes") or {}).get(args.runtime)
    if runtime is None:
        print(f"unknown runtime: {args.runtime}", file=sys.stderr); return 2
    try:
        env_name = key_env_name(runtime)
        staged, persistent = key_file_path(runtime), key_persist_path(runtime)
    except RuntimeError as error:
        print(f"{args.runtime} uses CLI/account authentication: {error}")
        return 1
    if args.action == "status":
        source = (f"environment ${env_name}" if os.environ.get(env_name) else
                  str(staged) if staged.exists() else
                  str(persistent) if persistent.exists() else None)
        print(f"{args.runtime}: {'configured via ' + source if source else 'missing'}")
        return 0 if source else 1
    if args.action == "remove":
        removed = []
        for path in (staged, persistent):
            if path.exists(): path.unlink(); removed.append(str(path))
        print("removed: " + (", ".join(removed) if removed else "nothing stored"))
        return 0
    value = getpass.getpass(f"{env_name}: ").strip()
    if not value:
        print("empty credential; nothing stored", file=sys.stderr); return 2
    persistent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    persistent.write_text(value); persistent.chmod(0o600)
    if staged.parent.exists():
        staged.write_text(value); staged.chmod(0o600)
    print(f"stored {args.runtime} credential in {persistent} (mode 0600)")
    return 0


def doctor_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner.py doctor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = []
    for role, runtime in (("author", get_teacher()), ("judge", get_judge())):
        try:
            runtime.preflight(require_auth=True)
            checks.append({"check": role, "ok": True,
                           "detail": f"{runtime.name}/{runtime.role['model']} ready"})
        except BaseException as error:  # adapters commonly raise SystemExit
            checks.append({"check": role, "ok": False, "detail": str(error)})
    checks.extend([
        {"check": "git", "ok": shutil.which("git") is not None,
         "detail": shutil.which("git") or "not found"},
        {"check": "storage", "ok": STORAGE_ROOT.exists(), "detail": str(STORAGE_ROOT)},
        {"check": "seeds", "ok": bool(list(SEEDS_DIR.glob('*/task.json'))),
         "detail": f"{len(list(SEEDS_DIR.glob('*/task.json')))} available at {SEEDS_DIR}"},
    ])
    if args.json:
        import json; print(json.dumps(checks, indent=2))
    else:
        for check in checks:
            print(f"[{'ok' if check['ok'] else 'FAIL':4}] {check['check']}: {check['detail']}")
    return 0 if all(c["ok"] for c in checks) else 1
