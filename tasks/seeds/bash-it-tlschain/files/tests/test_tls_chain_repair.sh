#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
cleanup() {
  rm -rf -- "$test_root"
}
trap cleanup EXIT

new_case() {
  local name=$1
  local case_dir=$test_root/$name
  mkdir -p "$case_dir/certs/intermediates" "$case_dir/certs/roots" "$case_dir/certs/private" "$case_dir/deploy"
  cp -- "$project_root/certs/leaf.pem" "$case_dir/certs/leaf.pem"
  cp -- "$project_root/certs/intermediates/issuing.pem" "$case_dir/certs/intermediates/issuing.pem"
  cp -- "$project_root/certs/roots/root.pem" "$case_dir/certs/roots/root.pem"
  cp -- "$project_root/deploy/server-chain.pem" "$case_dir/deploy/server-chain.pem"
  printf '%s\n' 'PRIVATE_KEY_SENTINEL_14NET3' > "$case_dir/certs/private/server.key"
  printf '%s\n' "$case_dir"
}

run_repair() {
  local case_dir=$1
  local name=$2
  local epoch=$3
  "$project_root/bin/tls-chain-repair" \
    --leaf "$case_dir/certs/leaf.pem" \
    --intermediates "$case_dir/certs/intermediates" \
    --roots "$case_dir/certs/roots" \
    --hostname "$name" \
    --at "$epoch" \
    --bundle "$case_dir/deploy/server-chain.pem" \
    --rollback-doc "$case_dir/deploy/ROLLBACK.md"
}

fingerprint() {
  openssl x509 -in "$1" -noout -fingerprint -sha256 | sed 's/^sha256 Fingerprint=//; s/://g'
}

success_case=$(new_case success)
cp -- "$success_case/deploy/server-chain.pem" "$success_case/original.pem"
original_sha=$(sha256sum "$success_case/original.pem" | awk '{print $1}')

if ! run_repair "$success_case" api.example.test 1784980800 > "$success_case/repair.log" 2>&1; then
  echo "TLS chain repair unexpectedly failed:" >&2
  sed -n '1,160p' "$success_case/repair.log" >&2
  exit 1
fi

grep -Fq 'certificate role=leaf file=leaf.pem' "$success_case/repair.log"
grep -Fq 'certificate role=intermediate-candidate file=issuing.pem' "$success_case/repair.log"
grep -Fq 'certificate role=root-candidate file=root.pem' "$success_case/repair.log"
grep -Fq 'current bundle order=invalid' "$success_case/repair.log"
grep -Fq 'validity verified at=1784980800' "$success_case/repair.log"
grep -Fq 'trust verified root=root.pem' "$success_case/repair.log"
grep -Fq 'hostname verified name=api.example.test' "$success_case/repair.log"
grep -Fq 'served bundle verified order=leaf,intermediate certificates=2 root=excluded' "$success_case/repair.log"

[[ $(grep -c -- '^-----BEGIN CERTIFICATE-----$' "$success_case/deploy/server-chain.pem") -eq 2 ]] || {
  echo "served bundle must contain exactly two certificates" >&2
  exit 1
}

awk -v destination="$success_case" '
  /^-----BEGIN CERTIFICATE-----$/ { count++; output = sprintf("%s/served-%d.pem", destination, count) }
  count { print > output }
  /^-----END CERTIFICATE-----$/ { close(output) }
' "$success_case/deploy/server-chain.pem"

[[ $(fingerprint "$success_case/served-1.pem") == "$(fingerprint "$success_case/certs/leaf.pem")" ]] || {
  echo "the leaf is not first in the served bundle" >&2
  exit 1
}
[[ $(fingerprint "$success_case/served-2.pem") == "$(fingerprint "$success_case/certs/intermediates/issuing.pem")" ]] || {
  echo "the issuing intermediate is not second in the served bundle" >&2
  exit 1
}
[[ $(fingerprint "$success_case/served-1.pem") != "$(fingerprint "$success_case/certs/roots/root.pem")" && $(fingerprint "$success_case/served-2.pem") != "$(fingerprint "$success_case/certs/roots/root.pem")" ]] || {
  echo "the trust root was incorrectly included in the served bundle" >&2
  exit 1
}

openssl verify -attime 1784980800 \
  -CAfile "$success_case/certs/roots/root.pem" \
  -untrusted "$success_case/deploy/server-chain.pem" \
  -verify_hostname api.example.test \
  "$success_case/certs/leaf.pem" > /dev/null

cmp -s "$success_case/original.pem" "$success_case/deploy/server-chain.pem.rollback" || {
  echo "rollback artifact does not preserve the previous bundle" >&2
  exit 1
}
grep -Fq "Previous bundle SHA-256: $original_sha" "$success_case/deploy/ROLLBACK.md"
grep -Fq 'Rollback artifact: server-chain.pem.rollback' "$success_case/deploy/ROLLBACK.md"
grep -Fq "cp -- 'server-chain.pem.rollback' 'server-chain.pem'" "$success_case/deploy/ROLLBACK.md"

[[ $(<"$success_case/certs/private/server.key") == 'PRIVATE_KEY_SENTINEL_14NET3' ]] || {
  echo "private-key fixture was modified" >&2
  exit 1
}
if grep -R -Fq 'PRIVATE_KEY_SENTINEL_14NET3' "$success_case/deploy" "$success_case/repair.log"; then
  echo "private-key material escaped into output" >&2
  exit 1
fi
if grep -Eq -- '-----BEGIN ([A-Z0-9]+ )?PRIVATE KEY-----' "$success_case/deploy/server-chain.pem"; then
  echo "private-key material was included in the served bundle" >&2
  exit 1
fi

hostname_case=$(new_case wrong-hostname)
cp -- "$hostname_case/deploy/server-chain.pem" "$hostname_case/before.pem"
if run_repair "$hostname_case" wrong.example.test 1784980800 > "$hostname_case/repair.log" 2>&1; then
  echo "repair accepted the wrong hostname" >&2
  exit 1
fi
grep -Fq 'hostname verification failed for wrong.example.test' "$hostname_case/repair.log"
cmp -s "$hostname_case/before.pem" "$hostname_case/deploy/server-chain.pem" || {
  echo "hostname failure changed the deployed bundle" >&2
  exit 1
}
[[ ! -e "$hostname_case/deploy/server-chain.pem.rollback" && ! -e "$hostname_case/deploy/ROLLBACK.md" ]] || {
  echo "hostname failure created rollback output" >&2
  exit 1
}

time_case=$(new_case outside-validity)
cp -- "$time_case/deploy/server-chain.pem" "$time_case/before.pem"
if run_repair "$time_case" api.example.test 1784563200 > "$time_case/repair.log" 2>&1; then
  echo "repair accepted certificates outside their validity window" >&2
  exit 1
fi
grep -Fq 'certificate validation failed at epoch 1784563200' "$time_case/repair.log"
cmp -s "$time_case/before.pem" "$time_case/deploy/server-chain.pem" || {
  echo "validity failure changed the deployed bundle" >&2
  exit 1
}
[[ ! -e "$time_case/deploy/server-chain.pem.rollback" && ! -e "$time_case/deploy/ROLLBACK.md" ]] || {
  echo "validity failure created rollback output" >&2
  exit 1
}

echo "TLS chain repair tests passed"
