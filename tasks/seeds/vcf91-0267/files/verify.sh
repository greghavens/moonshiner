#!/usr/bin/env bash
# Protected verification for the VCF Operations report client.
#
# Everything runs against the in-process mock in internal/mockops. No live
# VMware endpoint is contacted and no network access is required.
set -euo pipefail

cd "$(dirname "$0")"

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
