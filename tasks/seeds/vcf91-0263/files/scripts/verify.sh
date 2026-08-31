#!/usr/bin/env bash
# Runs the task's checks.
# appliance stand-in in tools/vcfops_mock.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

exec python3 -m unittest discover -s tests -p 'test_*.py' -t . -v
