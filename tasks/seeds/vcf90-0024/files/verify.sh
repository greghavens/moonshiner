#!/usr/bin/env bash
# Protected acceptance run. Do not edit.
#
# Everything here is offline: GOPROXY is disabled so the build must resolve
# against the standard library alone, and the only HTTP the tests perform goes
# to the loopback mock in internal/mockvcf. No VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

export GOTOOLCHAIN=local
export GOPROXY=off
export GOFLAGS=-mod=mod
export CGO_ENABLED=1

for f in docs/contract.json docs/official_sources.json internal/mockvcf/mockvcf.go \
         internal/mockvcf/scenario.go internal/verify/verify_test.go; do
  [ -f "$f" ] || { echo "FAIL: protected file $f is missing"; exit 1; }
done

if [ ! -d sddcdiag ]; then
  echo "FAIL: package directory sddcdiag/ does not exist"
  exit 1
fi

echo "== go vet =="
go vet ./...

echo "== go test -race =="
go test -race -count=1 ./...

echo "OK"
