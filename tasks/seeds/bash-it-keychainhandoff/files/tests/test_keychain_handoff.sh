#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case_dir=$(mktemp -d)
handoff_pid=
reader_pid=
cleanup() {
  [[ -z "$handoff_pid" ]] || kill "$handoff_pid" 2>/dev/null || true
  [[ -z "$reader_pid" ]] || kill "$reader_pid" 2>/dev/null || true
  chmod -R u+rwx "$case_dir" 2>/dev/null || true
  rm -rf -- "$case_dir"
}
trap cleanup EXIT

mkdir -p "$case_dir/keychain/private" "$case_dir/references" "$case_dir/state"
cp "$project_root/keychain/identities.tsv" "$case_dir/keychain/identities.tsv"
cp "$project_root/keychain/trust.tsv" "$case_dir/keychain/trust.tsv"
cp "$project_root/references/approved.certref" "$case_dir/references/approved.certref"
cp "$project_root/references/unapproved.certref" "$case_dir/references/unapproved.certref"

private_sentinel='PRIVATE_MATERIAL_MUST_NOT_ESCAPE_7f89d20c'
printf '%s\n' "$private_sentinel" > "$case_dir/keychain/private/api.internal.example.key"
historical_rollback="1729999000"$'\t'"approved-reference"$'\t'"9999999999999999999999999999999999999999999999999999999999999999"$'\t'"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
printf '%s\n' "$historical_rollback" > "$case_dir/state/rollback.log"

cp "$case_dir/keychain/identities.tsv" "$case_dir/identities.before"
cp "$case_dir/keychain/trust.tsv" "$case_dir/trust.before"
cp "$case_dir/keychain/private/api.internal.example.key" "$case_dir/private.before"
cp "$case_dir/references/unapproved.certref" "$case_dir/unapproved.before"

while IFS=$'\t' read -r fingerprint subject not_before not_after key_usage identity_state; do
  [[ -n "$fingerprint" && "$fingerprint" != \#* ]] || continue
  printf 'identity fingerprint=%s subject=%s not_before=%s not_after=%s usage=%s state=%s\n' \
    "$fingerprint" "$subject" "$not_before" "$not_after" "$key_usage" "$identity_state"
done < "$case_dir/keychain/identities.tsv" > "$case_dir/expected-command.log"
expected_fingerprint=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
printf 'selected fingerprint=%s not_after=1760000000\n' "$expected_fingerprint" >> "$case_dir/expected-command.log"
printf 'trust verified fingerprint=%s\n' "$expected_fingerprint" >> "$case_dir/expected-command.log"

# An unreadable private directory makes accidental traversal fail without ever
# giving the command access to the sentinel contents.
chmod 000 "$case_dir/keychain/private"
handoff_status=0
"$project_root/bin/keychain-handoff" \
  --metadata "$case_dir/keychain/identities.tsv" \
  --trust-store "$case_dir/keychain/trust.tsv" \
  --approved-reference "$case_dir/references/approved.certref" \
  --rollback-log "$case_dir/state/rollback.log" \
  --now 1730000000 > "$case_dir/command.log" 2>&1 || handoff_status=$?
chmod 700 "$case_dir/keychain/private"

if ((handoff_status != 0)); then
  printf 'handoff unexpectedly failed:\n' >&2
  sed -n '1,120p' "$case_dir/command.log" >&2
  exit 1
fi

actual_fingerprint=$(tr -d '\r\n' < "$case_dir/references/approved.certref")
[[ "$actual_fingerprint" == "$expected_fingerprint" ]] || {
  echo "approved reference selected $actual_fingerprint, expected $expected_fingerprint" >&2
  exit 1
}

expected_rollback="$historical_rollback"$'\n'"1730000000"$'\t'"approved-reference"$'\t'"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"$'\t'"$expected_fingerprint"
actual_rollback=$(tr -d '\r' < "$case_dir/state/rollback.log")
[[ "$actual_rollback" == "$expected_rollback" ]] || {
  echo "rollback record was not appended correctly" >&2
  exit 1
}

if ! cmp -s "$case_dir/expected-command.log" "$case_dir/command.log"; then
  echo "command output changed unexpectedly" >&2
  diff -u "$case_dir/expected-command.log" "$case_dir/command.log" >&2 || true
  exit 1
fi
if grep -Fq "$private_sentinel" "$case_dir/command.log"; then
  echo "private material was exposed in command output" >&2
  exit 1
fi

cmp -s "$case_dir/identities.before" "$case_dir/keychain/identities.tsv" || {
  echo "identity metadata was modified" >&2
  exit 1
}
cmp -s "$case_dir/trust.before" "$case_dir/keychain/trust.tsv" || {
  echo "trust metadata was modified" >&2
  exit 1
}
cmp -s "$case_dir/private.before" "$case_dir/keychain/private/api.internal.example.key" || {
  echo "private material was modified" >&2
  exit 1
}
cmp -s "$case_dir/unapproved.before" "$case_dir/references/unapproved.certref" || {
  echo "an unapproved reference was modified" >&2
  exit 1
}
reference_temps=("$case_dir/references/approved.certref.tmp."*)
[[ ! -e "${reference_temps[0]}" ]] || {
  echo "a temporary approved-reference file was left behind" >&2
  exit 1
}

# A second data set prevents fixture-specific selection and proves that trust
# belongs to the replacement: the current identity is deliberately untrusted.
alternate_dir="$case_dir/alternate"
mkdir -p "$alternate_dir/references" "$alternate_dir/state"
alternate_current=$(printf '3%.0s' {1..64})
alternate_older=$(printf '4%.0s' {1..64})
alternate_best=$(printf '5%.0s' {1..64})
alternate_untrusted=$(printf '6%.0s' {1..64})
alternate_other_subject=$(printf '7%.0s' {1..64})
alternate_wrong_usage=$(printf '8%.0s' {1..64})
alternate_revoked=$(printf '9%.0s' {1..64})
alternate_future=$(printf 'A%.0s' {1..64})
{
  printf '# fingerprint\tsubject\tnot_before\tnot_after\tkey_usage\tstate\n'
  printf '%s\tservice.alt.example\t1680000000\t1731000000\tclientAuth\tactive\n' "$alternate_current"
  printf '%s\tservice.alt.example\t1710000000\t1740000000\tclientAuth\tactive\n' "$alternate_older"
  printf '%s\tservice.alt.example\t1720000000\t1770000000\tclientAuth\tactive\n' "$alternate_best"
  printf '%s\tservice.alt.example\t1720000000\t1800000000\tclientAuth\tactive\n' "$alternate_untrusted"
  printf '%s\tother.alt.example\t1720000000\t1900000000\tclientAuth\tactive\n' "$alternate_other_subject"
  printf '%s\tservice.alt.example\t1720000000\t2000000000\tserverAuth\tactive\n' "$alternate_wrong_usage"
  printf '%s\tservice.alt.example\t1720000000\t2100000000\tclientAuth\trevoked\n' "$alternate_revoked"
  printf '%s\tservice.alt.example\t1740000000\t2200000000\tclientAuth\tactive\n' "$alternate_future"
} > "$alternate_dir/identities.tsv"
{
  printf '# fingerprint\ttrust_state\n'
  printf '%s\tuntrusted\n' "$alternate_current"
  printf '%s\ttrusted\n' "$alternate_older"
  printf '%s\ttrusted\n' "$alternate_best"
  printf '%s\tuntrusted\n' "$alternate_untrusted"
  printf '%s\ttrusted\n' "$alternate_other_subject"
  printf '%s\ttrusted\n' "$alternate_wrong_usage"
  printf '%s\ttrusted\n' "$alternate_revoked"
  printf '%s\ttrusted\n' "$alternate_future"
} > "$alternate_dir/trust.tsv"
printf '%s\n' "$alternate_current" > "$alternate_dir/references/approved.certref"
printf '%s\n' "$alternate_untrusted" > "$alternate_dir/references/unapproved.certref"
printf '' > "$alternate_dir/state/rollback.log"
cp "$alternate_dir/identities.tsv" "$alternate_dir/identities.before"
cp "$alternate_dir/trust.tsv" "$alternate_dir/trust.before"
cp "$alternate_dir/references/unapproved.certref" "$alternate_dir/unapproved.before"

alternate_status=0
"$project_root/bin/keychain-handoff" \
  --metadata "$alternate_dir/identities.tsv" \
  --trust-store "$alternate_dir/trust.tsv" \
  --approved-reference "$alternate_dir/references/approved.certref" \
  --rollback-log "$alternate_dir/state/rollback.log" \
  --now 1730000000 > "$alternate_dir/command.log" 2>&1 || alternate_status=$?
if ((alternate_status != 0)); then
  echo "alternate handoff unexpectedly failed" >&2
  sed -n '1,120p' "$alternate_dir/command.log" >&2
  exit 1
fi
alternate_actual=$(tr -d '\r\n' < "$alternate_dir/references/approved.certref")
[[ "$alternate_actual" == "$alternate_best" ]] || {
  echo "alternate handoff selected $alternate_actual, expected $alternate_best" >&2
  exit 1
}
grep -Fq "selected fingerprint=$alternate_best not_after=1770000000" "$alternate_dir/command.log"
grep -Fq "trust verified fingerprint=$alternate_best" "$alternate_dir/command.log"
alternate_expected_rollback="1730000000"$'\t'"approved-reference"$'\t'"$alternate_current"$'\t'"$alternate_best"
alternate_actual_rollback=$(tr -d '\r\n' < "$alternate_dir/state/rollback.log")
[[ "$alternate_actual_rollback" == "$alternate_expected_rollback" ]] || {
  echo "alternate handoff wrote an incorrect rollback record" >&2
  exit 1
}
cmp -s "$alternate_dir/identities.before" "$alternate_dir/identities.tsv" || {
  echo "alternate handoff modified identity metadata" >&2
  exit 1
}
cmp -s "$alternate_dir/trust.before" "$alternate_dir/trust.tsv" || {
  echo "alternate handoff modified trust metadata" >&2
  exit 1
}
cmp -s "$alternate_dir/unapproved.before" "$alternate_dir/references/unapproved.certref" || {
  echo "alternate handoff modified an unapproved reference" >&2
  exit 1
}

# Force trust to change after selection but before the update. The FIFO blocks
# the rollback append at a deterministic point, allowing the test to revoke the
# chosen fingerprint and exercise the required restoration path without sleeps.
failure_dir="$case_dir/failure"
mkdir -p "$failure_dir/references" "$failure_dir/state"
cp "$project_root/keychain/identities.tsv" "$failure_dir/identities.tsv"
cp "$project_root/keychain/trust.tsv" "$failure_dir/trust.tsv"
cp "$project_root/references/approved.certref" "$failure_dir/references/approved.certref"
cp "$project_root/references/unapproved.certref" "$failure_dir/references/unapproved.certref"
mkfifo "$failure_dir/state/rollback.fifo"

(
  status=0
  "$project_root/bin/keychain-handoff" \
    --metadata "$failure_dir/identities.tsv" \
    --trust-store "$failure_dir/trust.tsv" \
    --approved-reference "$failure_dir/references/approved.certref" \
    --rollback-log "$failure_dir/state/rollback.fifo" \
    --now 1730000000 > "$failure_dir/command.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$failure_dir/exit.status"
  exit "$status"
) &
handoff_pid=$!

while ! grep -Fq "selected fingerprint=$expected_fingerprint not_after=1760000000" "$failure_dir/command.log" 2>/dev/null; do
  if [[ -f "$failure_dir/exit.status" ]]; then
    echo "verification-failure setup exited before selecting an identity" >&2
    sed -n '1,120p' "$failure_dir/command.log" >&2
    exit 1
  fi
done

sed $'s/^BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\ttrusted$/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\tuntrusted/' \
  "$failure_dir/trust.tsv" > "$failure_dir/trust.tsv.next"
mv -f -- "$failure_dir/trust.tsv.next" "$failure_dir/trust.tsv"
cat "$failure_dir/state/rollback.fifo" > "$failure_dir/rollback.record" &
reader_pid=$!

failure_status=0
wait "$handoff_pid" || failure_status=$?
handoff_pid=
wait "$reader_pid"
reader_pid=
((failure_status != 0)) || {
  echo "handoff succeeded after selected identity lost trust" >&2
  exit 1
}

restored_fingerprint=$(tr -d '\r\n' < "$failure_dir/references/approved.certref")
[[ "$restored_fingerprint" == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" ]] || {
  echo "verification failure did not restore the previous reference" >&2
  exit 1
}
expected_failure_rollback="1730000000"$'\t'"approved-reference"$'\t'"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"$'\t'"$expected_fingerprint"
actual_failure_rollback=$(tr -d '\r\n' < "$failure_dir/rollback.record")
[[ "$actual_failure_rollback" == "$expected_failure_rollback" ]] || {
  echo "verification failure wrote an incorrect rollback record" >&2
  exit 1
}
grep -Fq "selected identity failed trust verification; reference restored" "$failure_dir/command.log"
if grep -Fq "trust verified fingerprint=" "$failure_dir/command.log"; then
  echo "verification failure was reported as trusted" >&2
  exit 1
fi
cmp -s "$project_root/references/unapproved.certref" "$failure_dir/references/unapproved.certref" || {
  echo "verification failure modified an unapproved reference" >&2
  exit 1
}
failure_temps=("$failure_dir/references/approved.certref.tmp."*)
[[ ! -e "${failure_temps[0]}" ]] || {
  echo "verification failure left a temporary reference behind" >&2
  exit 1
}

echo "keychain handoff test passed"
