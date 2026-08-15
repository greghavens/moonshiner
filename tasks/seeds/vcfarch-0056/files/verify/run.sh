#!/usr/bin/env bash
# Protected acceptance check. All verification is offline.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
root="$PWD"

fail() {
	echo "VERIFY FAIL: $*" >&2
	exit 1
}

export GOPROXY=off
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local

# Phase 1 is deliberately the first check: validate the artifact through the
# installer OpenAPI's own SddcSpec schema before inspecting anything else.
go test -count=1 ./verify -run '^Test00SddcSpecConformsToInstallerSchema$' \
	|| fail "sddc-spec.json failed installer-schema validation"

# Only after schema validation do the integrity and semantic checks run.
(cd "$root" && sha256sum -c --quiet verify/checksums.sha256) \
	|| fail "a protected verifier, fixture, snapshot, specification, or module file was modified"

grep -q '^module example.com/vcfarch$' "$root/go.mod" \
	|| fail "go.mod module path must stay example.com/vcfarch"
if grep -qE '^[[:space:]]*require' "$root/go.mod"; then
	fail "go.mod declares a dependency; this module is standard-library only"
fi
[ ! -e "$root/go.sum" ] || fail "go.sum exists; this module is standard-library only"

mapfile -t package_tests < <(find "$root" -maxdepth 1 -name '*_test.go' -type f | sort)
[ "${#package_tests[@]}" -gt 0 ] || fail "the vcfarch package has no tests"
grep -q 't.Run(' "${package_tests[@]}" || fail "the vcfarch package tests are not table-driven"

unformatted="$(gofmt -l "$root/architecture.go" "$root/cmd" "$root/verify" "${package_tests[@]}")"
[ -z "$unformatted" ] || fail "Go files are not gofmt-formatted: $unformatted"

(cd "$root" && go vet ./...) || fail "go vet ./... failed"
(cd "$root" && go test -race -count=1 ./...) || fail "go test -race -count=1 ./... failed"

echo "VERIFY PASS"
