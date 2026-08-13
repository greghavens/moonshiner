#!/usr/bin/env bash
# Runs the protected contract verifier against the loopback mock.
# Standard library only; no network access beyond 127.0.0.1.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 tests/test_contract.py
