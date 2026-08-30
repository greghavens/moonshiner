#!/usr/bin/env bash
# Builds the module and runs the contract test: it starts the loopback mock,
# whose routes are built from docs/contract.json, drives the fleet install run
# against it and inspects every recorded request.
#
# No VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

export GOFLAGS=-mod=mod
export GOPROXY=off
export GOTOOLCHAIN=local
export GOWORK=off
export CGO_ENABLED=1

for doc in docs/contract.json docs/official_sources.json; do
  if [ ! -f "$doc" ]; then
    echo "verify: $doc is missing" >&2
    exit 1
  fi
done

echo "verify: building"
go build -buildvcs=false ./...

echo "verify: go vet"
go vet ./...

echo "verify: go test -race ./contracttest/..."
go test -race -count=1 ./contracttest/...

echo "verify: ok"
