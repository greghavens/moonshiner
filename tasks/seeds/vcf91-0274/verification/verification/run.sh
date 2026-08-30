#!/bin/sh
# Protected verification entry point.
#
# Everything below runs offline against the loopback mock in
# internal/opsmock; no VMware endpoint is contacted.
set -eu

cd "$(dirname "$0")/.."

export GOFLAGS=-mod=mod
export GOPROXY=off
export GOTOOLCHAIN=local
export CGO_ENABLED=1

fail() {
	echo "FAIL: $1" >&2
	exit 1
}

# ---------------------------------------------------------------- 0. artifacts
for f in docs/contract.json docs/official_sources.json opsdiag/opsdiag.go; do
	[ -f "$f" ] || fail "$f is missing"
done
[ -f opsdiag/opsdiag_test.go ] || fail "opsdiag/opsdiag_test.go is missing; ship table-driven tests for the package"

# ---------------------------------------------------------------- 1. build
echo "==> go vet ./..."
go vet ./... || fail "go vet reported problems"

# ---------------------------------------------------------------- 2. protected suite
echo "==> go test -race ./verification/"
go test -race -count=1 ./verification/ || fail "the protected acceptance suite did not pass"

# ---------------------------------------------------------------- 3. shipped tests
echo "==> go test -race -v ./opsdiag/"
out=$(mktemp)
trap 'rm -f "$out"' EXIT
if ! go test -race -count=1 -v ./opsdiag/ >"$out" 2>&1; then
	cat "$out" >&2
	fail "the tests shipped with the opsdiag package did not pass"
fi

subtests=$(grep -cE '^=== RUN +Test[A-Za-z0-9_]+/' "$out" || true)
[ "${subtests:-0}" -ge 3 ] || {
	cat "$out" >&2
	fail "opsdiag ships $subtests table-driven subtests, want at least 3"
}
echo "    $subtests table-driven subtests"

# ---------------------------------------------------------------- 4. whole module
echo "==> go test -race ./..."
go test -race -count=1 ./... || fail "go test -race ./... did not pass"

echo "PASS"
