#!/usr/bin/env bash
# CI entrypoint — protected file. The test runner itself is a pinned
# devDependency; the suite can only run out of the project's node_modules.
if [ ! -d node_modules ]; then
  echo "FAIL: node_modules/ not found — install the pinned dependencies from package-lock.json (npm ci)" >&2
  exit 1
fi
exec npx --no-install vitest run
