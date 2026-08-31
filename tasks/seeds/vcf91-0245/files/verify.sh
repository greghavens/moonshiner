#!/bin/sh
# Runs the task's checks.
set -e
cd "$(dirname "$0")"
exec python3 tests/test_wire_contract.py "$@"
