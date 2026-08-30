#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cache_dir="$(mktemp -d "$repo_dir/.verify-gocache.XXXXXX")"
cleanup() {
  rm -rf -- "$cache_dir"
}
trap cleanup EXIT

cd "$repo_dir"
CCACHE_DISABLE=1 GOCACHE="$cache_dir" go test -race ./...
