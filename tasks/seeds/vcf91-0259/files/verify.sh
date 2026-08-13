#!/usr/bin/env bash
# Protected verifier entry point. Runs the wire-contract suite against the
# loopback mock. Contacts nothing outside 127.0.0.1.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -t . -p 'test_*.py' -v
