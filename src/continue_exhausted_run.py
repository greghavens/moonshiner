#!/usr/bin/env python3
"""Wait for a legacy run to finish, then perform its configured final attempts."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time

from common import ROOT
from run_state import connect, now


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be positive")

    while True:
        db = connect()
        row = db.execute("SELECT status,limits_json FROM runs WHERE id=?",
                         (args.run,)).fetchone()
        if row is None:
            raise SystemExit(f"run not found: {args.run}")
        limits = json.loads(row["limits_json"])
        maximum = int(limits["max_attempts"])
        unfinished = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE run_id=? AND status IN "
            "('pending','retry','running')", (args.run,)).fetchone()[0]
        db.close()
        if not unfinished and row["status"] != "running":
            break
        time.sleep(args.poll_seconds)

    db = connect()
    db.execute("BEGIN IMMEDIATE")
    eligible = db.execute(
        "SELECT COUNT(*) FROM jobs WHERE run_id=? AND status='exhausted' "
        "AND attempts<?", (args.run, maximum)).fetchone()[0]
    if not eligible:
        db.rollback()
        print("no exhausted jobs are eligible for another attempt", flush=True)
        return 0
    db.execute(
        "UPDATE jobs SET status='retry',lease_owner=NULL,lease_expires_at=NULL,"
        "updated_at=? WHERE run_id=? AND status='exhausted' AND attempts<?",
        (now(), args.run, maximum))
    db.execute("UPDATE runs SET status='running',updated_at=?,error=NULL WHERE id=?",
               (now(), args.run))
    db.commit(); db.close()
    print(f"queued final attempt for {eligible} exhausted job(s)", flush=True)
    return subprocess.run([
        sys.executable, str(ROOT / "moonshiner.py"), "run", "--resume", args.run,
        "--yes",
    ], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
