#!/usr/bin/env bash
# Deterministic verification for this task. Runs entirely offline against the
# loopback mock; no VMware endpoint and no module proxy is contacted.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export GOPROXY=off
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

echo "==> protected files"
while read -r want path; do
  [ -n "${path:-}" ] || continue
  [ -f "$path" ] || fail "protected file $path is missing"
  got="$(sha256_of "$path")"
  [ "$got" = "$want" ] || fail "protected file $path was modified"
done < verification/PROTECTED.sha256
echo "    ok"

echo "==> deliverables"
for f in docs/contract.json docs/official_sources.json; do
  [ -f "$f" ] || fail "$f is missing"
done
for d in pkg/client pkg/mock; do
  [ -d "$d" ] || fail "$d is missing"
done
echo "    ok"

echo "==> the packages carry their own tests"
own_tests="$(find pkg -name '*_test.go' -type f | wc -l | tr -d ' ')"
[ "$own_tests" -ge 2 ] || fail "expected test files under pkg/client and pkg/mock, found $own_tests test file(s) under pkg/"
for d in pkg/client pkg/mock; do
  [ -n "$(find "$d" -name '*_test.go' -type f -print -quit)" ] || fail "$d has no test file"
done
echo "    ok"

echo "==> go vet"
go vet ./...
echo "    ok"

echo "==> go test -race"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
if ! go test -race -count=1 ./... 2>&1 | tee "$out"; then
  fail "go test -race failed"
fi
for p in vcfhosts/verification vcfhosts/pkg/client vcfhosts/pkg/mock; do
  grep -Eq "^ok[[:space:]]+${p}([[:space:]]|$)" "$out" || fail "$p has no passing tests"
done
if grep 'no test files' "$out" | grep -Eq 'vcfhosts/pkg/(client|mock)'; then
  fail "pkg/client and pkg/mock must have tests"
fi
echo "    ok"

echo
echo "PASS"
