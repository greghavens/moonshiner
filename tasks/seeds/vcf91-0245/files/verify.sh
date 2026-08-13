#!/bin/sh
# Protected verification for this task.  Runs entirely against the loopback mock.
set -e
cd "$(dirname "$0")"
exec python3 tests/test_wire_contract.py "$@"
