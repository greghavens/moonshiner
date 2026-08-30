#!/usr/bin/env bash
# Compile the client, run the TestMain harness against the loopback mock, and
# verify the wire shape of every request it made.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -B tools/verify.py "$@"
