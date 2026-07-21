#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export LC_ALL=C
export TZ=UTC

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

field_for() {
  local path=$1
  local column=$2
  local inventory=$3
  awk -F '\t' -v wanted_path="$path" -v wanted_column="$column" \
    '$1 == wanted_path { print $wanted_column; found = 1 } END { if (!found) exit 1 }' \
    "$inventory"
}

assert_field() {
  local path=$1
  local column=$2
  local expected=$3
  local inventory=$4
  local actual
  actual=$(field_for "$path" "$column" "$inventory") || fail "missing row for $path"
  [[ "$actual" == "$expected" ]] || \
    fail "$path column $column: expected '$expected', got '$actual'"
}

test_tmp=$(mktemp -d)
trap 'rm -rf -- "$test_tmp"' EXIT
inventory="$test_tmp/inventory.tsv"

run_inventory() {
  local renew_days=$1
  local output=$2

  ./certinventory \
    --at 2026-07-20T17:34:45Z \
    --renew-within "$renew_days" \
    --owners owners.tsv \
    fixtures > "$output"
}

run_inventory 30 "$inventory"

expected_header=$'path\tname\tissuer\texpires_utc\tdays_remaining\trenewal\tchain_use\tsha256\towner'
[[ "$(head -n 1 "$inventory")" == "$expected_header" ]] || fail 'TSV header changed'
[[ "$(wc -l < "$inventory")" -eq 6 ]] || fail 'expected one header and five certificate rows'
expected_paths=$'api.pem\nintermediate.pem\nlegacy.pem\nroot.pem\nworker.pem'
actual_paths=$(awk -F '\t' 'NR > 1 { print $1 }' "$inventory")
[[ "$actual_paths" == "$expected_paths" ]] || fail 'certificate path ordering changed'

# This leaf expires exactly 30 days after --at. Renewal windows are inclusive.
assert_field api.pem 2 api.example.test "$inventory"
assert_field api.pem 3 'Example Test Issuing CA' "$inventory"
assert_field api.pem 4 2026-08-19T17:34:45Z "$inventory"
assert_field api.pem 5 30 "$inventory"
assert_field api.pem 6 due "$inventory"
assert_field api.pem 7 leaf "$inventory"
assert_field api.pem 9 platform-team "$inventory"

assert_field legacy.pem 5 10 "$inventory"
assert_field legacy.pem 6 due "$inventory"
assert_field legacy.pem 9 legacy-systems "$inventory"
assert_field worker.pem 5 120 "$inventory"
assert_field worker.pem 6 ok "$inventory"
assert_field worker.pem 9 data-platform "$inventory"
assert_field intermediate.pem 3 'Example Test Root CA' "$inventory"
assert_field intermediate.pem 7 intermediate "$inventory"
assert_field intermediate.pem 9 security-pki "$inventory"
assert_field root.pem 3 'Example Test Root CA' "$inventory"
assert_field root.pem 7 root "$inventory"
assert_field root.pem 9 security-pki "$inventory"

# Exercise the inclusive boundary with other window sizes and certificates so
# the behavior is tied to the requested threshold rather than one fixture.
ten_day_inventory="$test_tmp/ten-days.tsv"
run_inventory 10 "$ten_day_inventory"
assert_field legacy.pem 5 10 "$ten_day_inventory"
assert_field legacy.pem 6 due "$ten_day_inventory"
assert_field api.pem 6 ok "$ten_day_inventory"

one_twenty_day_inventory="$test_tmp/one-twenty-days.tsv"
run_inventory 120 "$one_twenty_day_inventory"
assert_field worker.pem 5 120 "$one_twenty_day_inventory"
assert_field worker.pem 6 due "$one_twenty_day_inventory"

while IFS=$'\t' read -r path _name _issuer _expires _days _renewal _use fingerprint _owner; do
  [[ "$path" == path ]] && continue
  [[ "$fingerprint" =~ ^[0-9a-f]{64}$ ]] || fail "invalid SHA-256 for $path"
  expected_fingerprint=$(openssl x509 -in "fixtures/$path" -outform DER | sha256sum)
  expected_fingerprint=${expected_fingerprint%% *}
  [[ "$fingerprint" == "$expected_fingerprint" ]] || fail "wrong SHA-256 for $path"
done < "$inventory"

if grep -Eq -- '-----BEGIN .*PRIVATE KEY-----' fixtures/*.pem; then
  fail 'fixture directory contains private key material'
fi
if grep -q -- '-----BEGIN ' "$inventory"; then
  fail 'inventory copied PEM material into its report'
fi

# Certificate-like files without the .pem suffix are outside the inventory.
pem_only_dir="$test_tmp/pem-only"
mkdir "$pem_only_dir"
cp fixtures/api.pem "$pem_only_dir/included.pem"
cp fixtures/worker.pem "$pem_only_dir/ignored.crt"
pem_only_inventory="$test_tmp/pem-only.tsv"
./certinventory \
  --at 2026-07-20T17:34:45Z \
  --renew-within 30 \
  --owners owners.tsv \
  "$pem_only_dir" > "$pem_only_inventory"
[[ "$(wc -l < "$pem_only_inventory")" -eq 2 ]] || \
  fail 'inventoried a certificate without the .pem suffix'
assert_field included.pem 2 api.example.test "$pem_only_inventory"

# A certificate file containing a private-key PEM block must be rejected, and
# neither stdout nor stderr may reproduce that block.
private_dir="$test_tmp/private-input"
mkdir "$private_dir"
cp fixtures/api.pem "$private_dir/mixed.pem"
printf '%s\n' \
  '-----BEGIN PRIVATE KEY-----' \
  'test-only-private-key-marker' \
  '-----END PRIVATE KEY-----' >> "$private_dir/mixed.pem"
private_stdout="$test_tmp/private.stdout"
private_stderr="$test_tmp/private.stderr"
if ./certinventory \
    --at 2026-07-20T17:34:45Z \
    --renew-within 30 \
    --owners owners.tsv \
    "$private_dir" > "$private_stdout" 2> "$private_stderr"; then
  fail 'accepted a certificate file containing private-key material'
fi
if grep -q -- '-----BEGIN PRIVATE KEY-----' "$private_stdout" "$private_stderr"; then
  fail 'copied private-key material into output'
fi

printf 'PASS: certificate inventory metadata is correct\n'
