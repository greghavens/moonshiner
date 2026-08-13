#!/usr/bin/env bash
# Deterministic protected verification for vcf91-0234.
#
# Everything runs offline against the in-process contract fixture; no live VMware
# endpoint is contacted. GOPROXY=off makes any attempt to pull a module fail
# rather than reach the network.
set -euo pipefail

cd "$(dirname "$0")/.."

verification_tmp="$(mktemp -d)"
cleanup() {
  find "${verification_tmp}" -depth -delete
}
trap cleanup EXIT HUP INT TERM

export HOME="${verification_tmp}/home"
export XDG_CACHE_HOME="${verification_tmp}/xdg-cache"
export GOCACHE="${verification_tmp}/go-build"
export CCACHE_DISABLE=1
mkdir -p "${HOME}" "${XDG_CACHE_HOME}" "${GOCACHE}"

export GOFLAGS="-mod=readonly"
export GOPROXY=off
export GOSUMDB=off
export GOTOOLCHAIN=local
export CGO_ENABLED=1

echo "== build =="
go build ./...

echo "== stdlib-only check for ./sddclcm =="
external="$(go list -deps ./sddclcm | awk -F/ '$1 ~ /\./ {print}' || true)"
if [ -n "${external}" ]; then
  echo "sddclcm must depend on the standard library only, found:" >&2
  echo "${external}" >&2
  exit 1
fi

echo "== go test -race ./... =="
go test -race -count=1 ./...

echo "== table-driven subtest coverage in ./sddclcm =="
subtests="$(go test -race -count=1 -v ./sddclcm/ 2>&1 | grep -c -E '^[[:space:]]*--- PASS: .+/' || true)"
echo "passing subtests in ./sddclcm: ${subtests}"
if [ "${subtests}" -lt 6 ]; then
  echo "expected at least 6 passing table-driven subtests (t.Run cases) in ./sddclcm, got ${subtests}" >&2
  exit 1
fi

echo "OK"
