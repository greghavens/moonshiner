#!/usr/bin/env bash
# Deterministic verification for the vCenter onboarding client.
#
# Run from the repository root:   ./scripts/verify.sh
#
# No network access is required and no live VMware endpoint is contacted. The
# only server involved is the 127.0.0.1 mock in internal/mockni.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

# Keep all Go build artifacts inside a disposable directory. Verification must
# not depend on a writable home directory or on a pre-populated host cache.
verify_tmp="$(mktemp -d)" || exit 2
export GOCACHE="$verify_tmp/go-build"
export CCACHE_DIR="$verify_tmp/ccache"
export CCACHE_TEMPDIR="$verify_tmp/ccache-tmp"
trap 'rm -rf "$verify_tmp"' EXIT

fail=0
step() { printf '\n=== %s ===\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1"; fail=1; }
ok()   { printf 'ok: %s\n' "$1"; }

step "protected files are unmodified"
if ! command -v sha256sum >/dev/null 2>&1; then
  bad "sha256sum is not available; cannot verify the protected files"
else
  while read -r want path; do
    [ -z "${want:-}" ] && continue
    if [ ! -f "$path" ]; then
      bad "protected file is missing: $path"
      continue
    fi
    got="$(sha256sum "$path" | cut -d' ' -f1)"
    if [ "$got" != "$want" ]; then
      bad "protected file was modified: $path"
    else
      ok "$path"
    fi
  done < scripts/protected.sha256
fi

step "the onboarding package has its own tests"
shopt -s nullglob
onboarding_tests=(onboarding/*_test.go)
if [ ${#onboarding_tests[@]} -eq 0 ]; then
  bad "no test file found in onboarding/ (the package must ship table-driven tests)"
else
  ok "found ${#onboarding_tests[@]} test file(s) in onboarding/"
fi

step "go vet"
if go vet ./... 2>&1; then
  ok "go vet clean"
else
  bad "go vet reported problems"
fi

step "gofmt"
unformatted="$(gofmt -l . 2>&1)"
if [ -z "$unformatted" ]; then
  ok "repository is gofmt-clean"
else
  printf '%s\n' "$unformatted"
  bad "gofmt reported unformatted Go files"
fi

step "go test -race ./..."
if go test -race -count=1 ./... 2>&1; then
  ok "all tests pass under the race detector"
else
  bad "go test -race failed"
fi

step "onboarding package coverage from its own tests"
cov_profile="$verify_tmp/coverage.out"
cov_out="$(go test -race -count=1 -covermode=atomic -coverprofile="$cov_profile" ./onboarding/ 2>&1)"
printf '%s\n' "$cov_out"
pct="$(printf '%s\n' "$cov_out" | sed -n 's/.*coverage: \([0-9.]*\)% of statements.*/\1/p' | tail -1)"
if [ -z "$pct" ]; then
  bad "could not determine statement coverage for ./onboarding/"
else
  if awk "BEGIN{exit !($pct >= 70.0)}"; then
    ok "statement coverage ${pct}% (>= 70.0%)"
  else
    bad "statement coverage ${pct}% is below the required 70.0%"
  fi
fi

step "result"
if [ "$fail" -eq 0 ]; then
  echo "PASS"
  exit 0
fi
echo "FAILED"
exit 1
