#!/usr/bin/env bash
# CI gate for the signal-box crate: warning-clean build, then the tests.
set -euo pipefail
export RUSTFLAGS="-D warnings"
exec cargo test --offline
