#!/usr/bin/env bash
# Acceptance checks for the vcfops adapter registration client.
#
# Everything here runs offline: the HTTP traffic is served by the in-memory
# double in internal/opsmock and no VMware endpoint is contacted.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

export GOFLAGS="-mod=mod"
export GOPROXY=off
export GOWORK=off
export GOTOOLCHAIN=local
export GOCACHE="$ROOT/.gocache"
# Some managed runners install ccache as the C compiler while leaving its
# configured home-directory cache read-only. The race detector still uses the
# real compiler; disabling only ccache keeps the checks self-contained.
export CCACHE_DISABLE=1

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s ==\n' "$*"; }

# ---------------------------------------------------------------------------
step "Protected files are unmodified"
# ---------------------------------------------------------------------------
# internal/opsmock is the pinned contract double and verification/verify holds
# the acceptance tests. Neither may be edited by the change under test.
while read -r want path; do
  [ -n "$want" ] || continue
  [ -f "$path" ] || fail "protected file $path is missing"
  got="$(sha256sum "$path" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || fail "protected file $path was modified"
done <<'PROTECTED'
2ff5fcdc2c54dbbc2b2aed5c6e71e6b8614fa8f71a1fe8c8e494dabe2e0c75b7 internal/opsmock/mock.go
52d1a03a09b3777d6f409935f6994c6d8e84866a77895a559a0e78e5f007bed0 verification/verify/docs_test.go
d2eeaf7f36582b22e62541a1041d7b6294ca48c414cb9048fc949b8f4445571f verification/verify/wire_test.go
PROTECTED
echo "ok"

# ---------------------------------------------------------------------------
step "Required artifacts exist"
# ---------------------------------------------------------------------------
for f in docs/contract.json docs/official_sources.json opsadapter/client.go opsadapter/types.go; do
  [ -f "$f" ] || fail "missing $f"
done
python3 -c 'import json,sys; [json.load(open(p)) for p in sys.argv[1:]]' \
  docs/contract.json docs/official_sources.json || fail "docs JSON is not parseable"
echo "ok"

# ---------------------------------------------------------------------------
step "The package ships table-driven tests"
# ---------------------------------------------------------------------------
shopt -s nullglob
pkg_tests=(opsadapter/*_test.go)
shopt -u nullglob
[ ${#pkg_tests[@]} -gt 0 ] || fail "opsadapter has no _test.go files"

grep -qE '(\[\]struct \{|map\[string\]struct \{)' "${pkg_tests[@]}" \
  || fail "opsadapter tests are not table-driven (no table of cases found)"
grep -q 't.Run(' "${pkg_tests[@]}" \
  || fail "opsadapter tests do not run their table as subtests"
echo "ok"

# ---------------------------------------------------------------------------
step "gofmt and go vet"
# ---------------------------------------------------------------------------
unformatted="$(gofmt -l opsadapter internal verification 2>/dev/null)"
[ -z "$unformatted" ] || fail "not gofmt-clean:"$'\n'"$unformatted"
go vet ./... || fail "go vet reported problems"
echo "ok"

# ---------------------------------------------------------------------------
step "Package tests under the race detector"
# ---------------------------------------------------------------------------
pkg_log="$(mktemp)"
if ! go test -race -count=1 -v ./opsadapter/ 2>&1 | tee "$pkg_log"; then
  rm -f "$pkg_log"
  fail "go test -race ./opsadapter/ failed"
fi
if ! grep -q '^--- PASS: Test' "$pkg_log"; then
  rm -f "$pkg_log"
  fail "no package test functions ran"
fi
subtests="$(grep -c '^ *--- PASS: Test.*/' "$pkg_log")"
rm -f "$pkg_log"
[ "$subtests" -ge 3 ] || fail "expected at least 3 table cases to run, saw $subtests"
echo "ok"

# ---------------------------------------------------------------------------
step "Acceptance tests under the race detector"
# ---------------------------------------------------------------------------
go test -race -count=1 ./... || fail "go test -race ./... failed"
echo "ok"

printf '\nPASS\n'
