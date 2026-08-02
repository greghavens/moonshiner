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
# Run the suite against throwaway project state. A checkout is often also a
# live project, and a test that resolves STORAGE_ROOT/WORKSPACES would then be
# pointed at real traces, ledgers and workspaces. Isolating it means no test can
# reach them whatever it does.
if [ -d tests ] && ls tests/test_*.py >/dev/null 2>&1; then
  test_home=$(mktemp -d -t moonshiner-check-XXXXXX)
  trap 'rm -rf "$test_home"' EXIT
  MOONSHINER_HOME="$test_home" XDG_DATA_HOME="$test_home/model-data" \
    python3 -m unittest discover -s tests -v
else
  echo "(no tests yet)"
fi

echo "== seed-corpus audit =="
# One audit over the whole corpus: it prints a line per seed, so keep the
# summary and the composition line but fail on its rc.
audit_out=$(python3 src/audit_seeds.py)
echo "$audit_out" | tail -2

echo "check: OK"
