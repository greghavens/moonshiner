#!/usr/bin/env bash
# Acceptance checks. Everything runs against the loopback vCenter in
# internal/mockvc; no VMware endpoint is contacted.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

if ! verify_tmp=$(mktemp -d); then
        echo "FAIL: could not create the isolated verification workspace"
        exit 1
fi
trap 'rm -r -- "$verify_tmp"' EXIT
if ! mkdir -p "$verify_tmp/go-build" "$verify_tmp/go-tmp" "$verify_tmp/ccache" "$verify_tmp/tmp"; then
        echo "FAIL: could not initialize the isolated verification workspace"
        exit 1
fi

export GOPROXY=off
export GOFLAGS=-mod=mod
export CGO_ENABLED=1
export GOCACHE="$verify_tmp/go-build"
export GOTMPDIR="$verify_tmp/go-tmp"
export CCACHE_DIR="$verify_tmp/ccache"
export TMPDIR="$verify_tmp/tmp"
export GOENV=off
export GOTOOLCHAIN=local
export GOTELEMETRY=off

failed=0
step() {
        printf '\n== %s\n' "$1"
}
fail() {
        printf 'FAIL: %s\n' "$1"
        failed=1
}

step "protected files are unmodified"
if ! sha256sum --quiet --check verify/protected.sha256; then
        fail "a protected file was modified; the acceptance checks and the loopback vCenter are not part of the deliverable"
fi

step "required deliverables exist"
for f in docs/contract.json docs/official_sources.json; do
        [ -s "$f" ] || fail "$f is missing or empty"
done
if ! ls guestcust/*_test.go >/dev/null 2>&1; then
        fail "guestcust has no tests"
fi

step "gofmt"
unformatted=$(gofmt -l . 2>&1)
if [ -n "$unformatted" ]; then
        fail "not gofmt-clean: $unformatted"
fi

step "go vet"
if ! go vet ./...; then
        fail "go vet"
fi

step "go test -race ./guestcust/..."
if ! go test -race -count=1 ./guestcust/...; then
        fail "go test -race ./guestcust/..."
fi

step "go test -race ./verify/..."
if ! go test -race -count=1 ./verify/...; then
        fail "go test -race ./verify/..."
fi

printf '\n'
if [ "$failed" -ne 0 ]; then
        echo "VERIFICATION FAILED"
        exit 1
fi
echo "VERIFICATION PASSED"
