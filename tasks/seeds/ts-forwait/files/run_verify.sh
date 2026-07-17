#!/usr/bin/env bash
# CI entrypoint — protected file. The suite runs against the project's own
# installed node_modules; the environment is part of the definition of done.
if [ ! -d node_modules ]; then
  echo "FAIL: node_modules/ not found — install the pinned dependencies from package-lock.json (npm ci)" >&2
  exit 1
fi
exec node --test test_forwait.ts
