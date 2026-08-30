#!/usr/bin/env bash
# Protected verification for the vCenter virtual-machine reconfiguration client.
#
# Everything runs against the loopback appliance in internal/vcmock, which
# listens on 127.0.0.1 and builds its routing table from docs/contract.json. No
# live VMware endpoint is contacted and no module downloads are needed.
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
