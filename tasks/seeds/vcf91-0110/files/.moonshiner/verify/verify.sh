#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
protected_test="$project_root/vcenter/moonshiner_protected_test.go"

if [[ ! -f "$project_root/vcenter/client_test.go" ]]; then
  echo "missing required table-driven vcenter/client_test.go" >&2
  exit 1
fi

cleanup() {
  rm -f "$protected_test"
}
trap cleanup EXIT

cp "$project_root/.moonshiner/verify/client_protected_test.go" "$protected_test"
cd "$project_root"
GOTOOLCHAIN=local GOPROXY=off go test -race ./...
