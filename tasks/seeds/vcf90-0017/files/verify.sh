#!/usr/bin/env bash
# Protected verification for the SDDC Manager host-commissioning client.
#
# Everything runs against the loopback mock in internal/smmock, which listens on
# 127.0.0.1. No live VMware endpoint is contacted and no network access is
# required.
set -euo pipefail

cd "$(dirname "$0")"

shopt -s nullglob
package_tests=(hostcommission/*_test.go)
if ((${#package_tests[@]} == 0)); then
	echo "hostcommission must include package tests" >&2
	exit 1
fi

echo "==> gofmt"
unformatted="$(gofmt -l . 2>/dev/null || true)"
if [ -n "$unformatted" ]; then
	echo "these files are not gofmt-clean:" >&2
	echo "$unformatted" >&2
	exit 1
fi

echo "==> go vet"
go vet ./...

echo "==> go test -race ./..."
go test -race -count=1 ./...

echo
echo "PASS"
