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
grep -q '^module example.com/vcfdiag$' "$root/go.mod" \
	|| fail "go.mod module path must stay example.com/vcfdiag"
if grep -qE '^[[:space:]]*require' "$root/go.mod"; then
	fail "go.mod declares a dependency; this module must use the standard library only"
fi
[ -e "$root/go.sum" ] && fail "go.sum exists; this module must use the standard library only"

# --- the module's own table-driven tests ------------------------------------
mapfile -t agent_tests < <(
	find "$root" -name '*_test.go' \
		-not -path "$root/verify/*" \
		-not -path "$root/.git/*" | sort
)
[ "${#agent_tests[@]}" -gt 0 ] \
	|| fail "no tests outside verify/: the module needs its own table-driven tests"
grep -q 't.Run(' "${agent_tests[@]}" \
	|| fail "no subtests found in ${agent_tests[*]}: the tests must be table-driven"

# --- build, vet, test -------------------------------------------------------
(cd "$root" && go build ./...) || fail "go build ./... failed"
(cd "$root" && go vet ./...) || fail "go vet ./... failed"
(cd "$root" && go test -race -count=1 ./...) || fail "go test -race -count=1 ./... failed"

echo "VERIFY PASS"
