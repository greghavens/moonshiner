#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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

echo "==> installer SddcSpec validation (must run first)"
python3 tests/verify_schema.py

echo "==> protected seed inputs and checks"
while read -r want path; do
  [ -n "${path:-}" ] || continue
  [ -f "$path" ] || fail "protected file $path is missing"
  got="$(sha256_of "$path")"
  [ "$got" = "$want" ] || fail "protected file $path was modified"
done < verification/PROTECTED.sha256
echo "    ok"

echo "==> migration architecture"
python3 tests/verify_plan.py

echo
echo "PASS"
