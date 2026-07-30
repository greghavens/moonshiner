#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
protected_test="$project_root/vcenter/moonshiner_protected_test.go"

if [[ ! -d "$project_root/vcenter" ]]; then
  echo "missing required vcenter package" >&2
  exit 1
fi

cleanup() {
  rm -f "$protected_test"
}
trap cleanup EXIT

cp "$project_root/.moonshiner/verify/client_protected_test.go" "$protected_test"
cd "$project_root"
go test -race ./...
