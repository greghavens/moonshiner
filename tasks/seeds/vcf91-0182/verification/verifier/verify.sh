#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f go.mod || ! -f docs/contract.json || ! -f docs/official_sources.json ]]; then
  echo "protected contract inputs are missing" >&2
  exit 1
fi

if [[ -n "$(gofmt -l logforwarder/*.go internal/contractmock/*.go)" ]]; then
  echo "Go sources must be gofmt-formatted" >&2
  gofmt -l logforwarder/*.go internal/contractmock/*.go >&2
  exit 1
fi

go test -race -count=1 -timeout 45s ./...
