#!/usr/bin/env bash
# Runs the contract test: it builds the mock's routes from docs/contract.json,
# drives the restore drill against the mock on loopback and inspects every
# recorded request. No VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

export GOFLAGS=-mod=mod
export GOPROXY=off
export GOTOOLCHAIN=local
export GOWORK=off
export CGO_ENABLED=1

# Keep compiler caches out of the invoking user's home. Verification may run
# with a read-only or ephemeral home, and cache state must not affect results.
verify_cache_dir="$(mktemp -d ./.verify-cache.XXXXXX)"
verify_cache_root="$PWD/${verify_cache_dir#./}"
trap 'rm -rf -- "$verify_cache_root"' EXIT
export GOCACHE="$verify_cache_root/go-build"
export CCACHE_DIR="$verify_cache_root/ccache"
export CCACHE_TEMPDIR="$verify_cache_root/ccache-tmp"

for doc in docs/contract.json docs/official_sources.json; do
  if [ ! -f "$doc" ]; then
    echo "verify: $doc is missing" >&2
    exit 1
  fi
done

echo "verify: building"
go build -buildvcs=false ./...

echo "verify: go test -race ./contracttest/..."
go test -race -count=1 ./contracttest/...

echo "verify: ok"
