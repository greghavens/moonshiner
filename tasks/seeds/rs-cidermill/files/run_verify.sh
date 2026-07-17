#!/usr/bin/env bash
# CI gate for the press-day crate: the build must be warning-clean before
# the tests get to run.
set -euo pipefail
export RUSTFLAGS="-D warnings"
exec cargo test --offline
