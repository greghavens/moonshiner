#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f migration-plan.json ]]; then
  echo "verification failed: migration-plan.json is required" >&2
  exit 1
fi

python3 installer/verify_schema.py installer/plan.schema.json migration-plan.json
go test -race ./...
