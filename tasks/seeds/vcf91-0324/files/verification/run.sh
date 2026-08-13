#!/usr/bin/env bash
# Protected acceptance check.
#
# Runs entirely offline: the module has no external dependencies, so GOPROXY is
# switched off to prove it, and every request the suite makes goes to a mock
# bound to 127.0.0.1. No VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")/.."

verify_tmp="$(mktemp -d "${TMPDIR:-/tmp}/vcfauto-verify.XXXXXX")"
cleanup() {
	rm -rf -- "$verify_tmp"
}
trap cleanup EXIT
mkdir -p "$verify_tmp/go-build" "$verify_tmp/go-tmp" "$verify_tmp/ccache" "$verify_tmp/cache"

export GOPROXY=off
export GOFLAGS=-mod=mod
export CGO_ENABLED=1
export GOENV=off
export GOTOOLCHAIN=local
export GOCACHE="$verify_tmp/go-build"
export GOTMPDIR="$verify_tmp/go-tmp"
export CCACHE_DIR="$verify_tmp/ccache"
export XDG_CACHE_HOME="$verify_tmp/cache"

echo "== gofmt =="
unformatted="$(gofmt -l . || true)"
if [ -n "$unformatted" ]; then
	echo "not gofmt'd:" >&2
	echo "$unformatted" >&2
	exit 1
fi

echo "== go vet =="
go vet ./...

echo "== go test -race =="
go test -race -count=1 ./...

echo
echo "PASS"
