#!/usr/bin/env bash
# CI gate for the gate-badge crate. unused-lifetimes is allow-by-default,
# so it is raised to warn here; -D warnings then makes everything fatal.
set -euo pipefail
export RUSTFLAGS="-W unused-lifetimes -D warnings"
exec cargo test --offline
