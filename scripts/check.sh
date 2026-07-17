#!/usr/bin/env bash
# Offline sanity gate — byte-compile, unit tests, and seed-corpus audit.
# No model calls, no network: safe to run anytime, in CI, or before a commit.
#
#   scripts/check.sh
#
# Exits non-zero on the first failure so it can gate a commit or a pipeline run.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== byte-compile (src, moonshiner.py$( [ -d tests ] && echo ', tests')) =="
python3 -m compileall -q src moonshiner.py $( [ -d tests ] && echo tests )

echo "== unit tests =="
if [ -d tests ] && ls tests/test_*.py >/dev/null 2>&1; then
  python3 -m unittest discover -s tests -v
else
  echo "(no tests yet)"
fi

echo "== seed-corpus audit =="
# audit prints one line per seed; keep only the summary but fail on its rc.
audit_out=$(python3 moonshiner.py audit)
echo "$audit_out" | tail -1

echo "check: OK"
