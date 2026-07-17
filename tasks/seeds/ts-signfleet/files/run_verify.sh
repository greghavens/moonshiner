#!/usr/bin/env bash
# CI gate — protected file. The type-check must be clean before the suite
# runs; both use the repo-pinned toolchain out of node_modules.
set -e
if [ ! -x node_modules/.bin/tsc ]; then
  echo "FAIL: node_modules missing — install the pinned toolchain with 'npm ci' first" >&2
  exit 1
fi
node_modules/.bin/tsc -p tsconfig.json
exec node --test test_signfleet.ts
