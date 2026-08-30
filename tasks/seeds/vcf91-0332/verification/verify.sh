#!/usr/bin/env bash
# Compiles the project, runs TestMain against the loopback mock and checks the recorded traffic
# against docs/contract.json. Contacts no network endpoint.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 verify/verify.py "$@"
