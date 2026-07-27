#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f response.txt ]]; then
  echo "reference response is absent" >&2
  exit 1
fi
if [[ -e .runtime/calls.jsonl ]]; then
  echo "runtime evidence is not clean" >&2
  exit 1
fi

./bin/concierge reservation --id hos-143 >response.txt
