#!/usr/bin/env bash
# Protected acceptance check. Runs entirely offline against loopback.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
root="$PWD"

fail() {
	echo "VERIFY FAIL: $*" >&2
	exit 1
}

# Stdlib only, and no module downloads: nothing here may reach the network.
export GOPROXY=off
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local

# --- deliverables exist -----------------------------------------------------
for f in docs/contract.json docs/official_sources.json; do
	[ -s "$root/$f" ] || fail "$f is missing or empty"
done

# --- the protected checks are untouched -------------------------------------
if [ -f "$root/verify/checksums.sha256" ]; then
	(cd "$root" && sha256sum -c --quiet verify/checksums.sha256) \
		|| fail "the protected files under verify/ were modified"
fi

# --- go.mod is intact and dependency-free -----------------------------------
grep -q '^module example.com/vcfauto$' "$root/go.mod" \
	|| fail "go.mod module path must stay example.com/vcfauto"
if grep -qE '^[[:space:]]*require' "$root/go.mod"; then
	fail "go.mod declares a dependency; this module must use the standard library only"
fi
[ -e "$root/go.sum" ] && fail "go.sum exists; this module must use the standard library only"

# --- the module's own table-driven tests ------------------------------------
mapfile -t client_tests < <(
	find "$root" -maxdepth 1 -name '*_test.go' -type f | sort
)
mapfile -t mock_tests < <(
	find "$root/mockapi" -maxdepth 1 -name '*_test.go' -type f | sort
)
[ "${#client_tests[@]}" -gt 0 ] \
	|| fail "package vcfauto has no tests: both packages need table-driven tests"
[ "${#mock_tests[@]}" -gt 0 ] \
	|| fail "package mockapi has no tests: both packages need table-driven tests"
grep -q 't.Run(' "${client_tests[@]}" \
	|| fail "package vcfauto tests are not table-driven"
grep -q 't.Run(' "${mock_tests[@]}" \
	|| fail "package mockapi tests are not table-driven"

# --- build, vet, test -------------------------------------------------------
(cd "$root" && go build ./...) || fail "go build ./... failed"
(cd "$root" && go vet ./...) || fail "go vet ./... failed"
(cd "$root" && go test -race -count=1 ./...) || fail "go test -race -count=1 ./... failed"

echo "VERIFY PASS"
