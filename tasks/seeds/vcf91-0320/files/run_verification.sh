#!/usr/bin/env bash
# Runs the protected contract verifier against the loopback mock.
# Standard library only; no network access beyond 127.0.0.1.
set -euo pipefail
cd "$(dirname "$0")"
# -S keeps host-installed site packages off sys.path, making the
# standard-library-only constraint part of the executable contract.
exec python3 -S tests/test_contract.py
