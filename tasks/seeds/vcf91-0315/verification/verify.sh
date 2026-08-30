#!/usr/bin/env bash
# PROTECTED FILE -- do not modify.
#
# Verifies that the protected files are untouched, then runs the acceptance
# tests. No network socket is opened and no VMware endpoint is contacted: the
# tests exchange raw HTTP bytes with an in-process fixture.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "== checking protected file integrity =="
"$PYTHON" -I - <<'PY'
import hashlib
import sys

failures = []
with open("PROTECTED.sha256", "r", encoding="utf-8") as manifest:
    for line in manifest:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expected, path = line.split(None, 1)
        path = path.lstrip("*").strip()
        try:
            with open(path, "rb") as handle:
                actual = hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            failures.append("{0}: {1}".format(path, exc))
            continue
        if actual != expected:
            failures.append("{0}: content changed".format(path))
        else:
            print("  ok  {0}".format(path))

if failures:
    print("\nprotected files were modified or removed:", file=sys.stderr)
    for failure in failures:
        print("  " + failure, file=sys.stderr)
    raise SystemExit(1)
PY

echo
echo "== running acceptance tests =="
"$PYTHON" -I - <<'PY'
import os
import sys
import unittest

# Isolated mode prevents candidate-added modules from shadowing the standard
# library. Append (rather than prepend) the workspace solely for package/tests.
sys.path.append(os.getcwd())
suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
