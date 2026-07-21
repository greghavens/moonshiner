#!/usr/bin/env bash

set -u -o pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
rollout="$project_root/bin/package-rollout"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

OLD_CHECKSUM=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

fail() {
  printf '    %s\n' "$1" >&2
  return 1
}

assert_eq() {
  local want=$1 got=$2 message=$3
  [[ "$want" == "$got" ]] || fail "$message (want '$want', got '$got')"
}

assert_file_contains() {
  local path=$1 text=$2
  [[ -f "$path" ]] || fail "missing file: $path" || return 1
  grep -Fq -- "$text" "$path" || fail "$path does not contain: $text"
}

assert_file_absent() {
  local path=$1
  [[ ! -e "$path" ]] || fail "file should not exist: $path"
}

make_fixture() {
  local root=$1 host
  mkdir -p "$root/state" "$root/artifacts"
  printf 'web-1\trole=web,env=prod\nweb-2\trole=web,env=prod\ndb-1\trole=db,env=prod\n' >"$root/inventory.tsv"
  for host in web-1 web-2 db-1; do
    mkdir -p "$root/state/$host"
    printf '1.0.0\n' >"$root/state/$host/package.version"
    printf '%s\n' "$OLD_CHECKSUM" >"$root/state/$host/package.sha256"
  done
  printf 'version=2.0.0\npayload=release-two\n' >"$root/artifacts/release.pkg"
  sha256sum "$root/artifacts/release.pkg" | awk '{print $1}' >"$root/artifacts/release.pkg.sha256"
}

version_of() {
  local root=$1 host=$2
  IFS= read -r value <"$root/state/$host/package.version"
  printf '%s' "$value"
}

snapshot_state() {
  local root=$1
  (
    cd "$root"
    find state -type f -print0 | sort -z | xargs -0 sha256sum
  )
}

invoke() {
  local root=$1 query=$2 concurrency=$3
  shift 3
  "$rollout" \
    --inventory "$root/inventory.tsv" \
    --state-dir "$root/state" \
    --artifact "$root/artifacts/release.pkg" \
    --query "$query" \
    --max-concurrency "$concurrency" \
    "$@"
}

test_successful_targeted_rollout() {
  local root="$scratch/success" output hash
  make_fixture "$root"
  output=$(invoke "$root" 'role=web,env=prod' 2) || return 1
  hash=$(sha256sum "$root/artifacts/release.pkg")
  hash=${hash%% *}

  assert_eq 2.0.0 "$(version_of "$root" web-1)" 'web-1 was not upgraded' || return 1
  assert_eq 2.0.0 "$(version_of "$root" web-2)" 'web-2 was not upgraded' || return 1
  assert_eq 1.0.0 "$(version_of "$root" db-1)" 'untargeted db-1 changed' || return 1
  assert_eq "$hash" "$(<"$root/state/web-1/package.sha256")" 'installed checksum is wrong' || return 1
  assert_file_absent "$root/state/db-1/events.log" || return 1
  assert_file_contains "$root/state/rollout-journal.tsv" $'web-1\tinstalled\t1.0.0' || return 1
  assert_file_contains "$root/state/rollout-journal.tsv" $'-\tcommitted\t2.0.0' || return 1
  [[ "$output" == *'COMMITTED version=2.0.0 hosts=2'* ]] || fail 'commit summary is missing'
}

test_dry_run_is_read_only() {
  local root="$scratch/dry-run" before after output
  make_fixture "$root"
  before=$(snapshot_state "$root")
  output=$(invoke "$root" 'role=web' 1 --dry-run 2>&1) || return 1
  after=$(snapshot_state "$root")

  assert_eq "$before" "$after" 'dry-run modified host state' || return 1
  assert_file_absent "$root/state/rollout-journal.tsv" || return 1
  [[ "$output" == *'TARGETS count=2 query=role=web'* ]] || fail 'dry-run target summary is missing' || return 1
  [[ "$output" == *'DRY-RUN no changes applied'* ]] || fail 'dry-run completion message is missing'
}

test_checksum_failure_precedes_mutation() {
  local root="$scratch/checksum" before after output status
  make_fixture "$root"
  printf '%064d\n' 0 >"$root/artifacts/release.pkg.sha256"
  before=$(snapshot_state "$root")
  output=$(invoke "$root" 'role=web' 2 2>&1)
  status=$?
  after=$(snapshot_state "$root")

  assert_eq 3 "$status" 'checksum mismatch returned the wrong status' || return 1
  assert_eq "$before" "$after" 'checksum mismatch modified state' || return 1
  assert_file_absent "$root/state/rollout-journal.tsv" || return 1
  [[ "$output" == *'artifact checksum mismatch'* ]] || fail 'checksum diagnostic is missing'
}

test_partial_install_rolls_back_only_changed_targets() {
  local root="$scratch/install-failure" output status
  make_fixture "$root"
  : >"$root/state/web-2/install.fail"
  output=$(invoke "$root" 'role=web' 2 2>&1)
  status=$?

  assert_eq 4 "$status" 'partial install failure returned the wrong status' || return 1
  assert_eq 1.0.0 "$(version_of "$root" web-1)" 'changed target was not rolled back' || return 1
  assert_eq 1.0.0 "$(version_of "$root" web-2)" 'failed target changed' || return 1
  assert_eq 1.0.0 "$(version_of "$root" db-1)" 'untargeted host version changed' || return 1
  assert_file_contains "$root/state/web-1/events.log" $'rollback\t1.0.0' || return 1
  assert_file_absent "$root/state/web-2/events.log" || return 1
  assert_file_absent "$root/state/db-1/events.log" || return 1
  if grep -q '^web-2' "$root/state/rollout-journal.tsv"; then
    fail 'rollback journal contains an install-failed target'
    return 1
  fi
  if grep -q '^db-1' "$root/state/rollout-journal.tsv"; then
    fail 'rollback journal contains an untargeted host'
    return 1
  fi
  [[ "$output" == *'install failed for host: web-2'* ]] || fail 'partial failure diagnostic is missing'
}

test_health_failure_rolls_back_installed_targets() {
  local root="$scratch/health-failure" output status
  make_fixture "$root"
  printf 'unhealthy\n' >"$root/state/web-2/health"
  output=$(invoke "$root" 'role=web' 2 2>&1)
  status=$?

  assert_eq 4 "$status" 'health failure returned the wrong status' || return 1
  assert_eq 1.0.0 "$(version_of "$root" web-1)" 'healthy peer was not rolled back' || return 1
  assert_eq 1.0.0 "$(version_of "$root" web-2)" 'unhealthy target was not rolled back' || return 1
  assert_file_contains "$root/state/web-1/events.log" $'install\t2.0.0' || return 1
  assert_file_contains "$root/state/web-2/events.log" $'rollback\t1.0.0' || return 1
  assert_file_absent "$root/state/db-1/events.log" || return 1
  assert_file_contains "$root/state/rollout-journal.tsv" $'-\tabort\thealth_failure' || return 1
  if grep -q '^db-1' "$root/state/rollout-journal.tsv"; then
    fail 'rollback journal contains an untargeted host after health failure'
    return 1
  fi
  [[ "$output" == *'health check failed for host: web-2'* ]] || fail 'health failure diagnostic is missing'
}

test_concurrency_is_bounded_in_batches() {
  local root="$scratch/concurrency" output
  make_fixture "$root"
  mkdir -p "$root/state/web-3"
  printf '1.0.0\n' >"$root/state/web-3/package.version"
  printf '%s\n' "$OLD_CHECKSUM" >"$root/state/web-3/package.sha256"
  printf 'web-3\trole=web,env=prod\n' >>"$root/inventory.tsv"
  output=$(invoke "$root" 'role=web' 2) || return 1

  assert_eq 2 "$(grep -c '^INSTALL-BATCH' <<<"$output")" 'unexpected install batch count' || return 1
  assert_eq 1 "$(grep -c '^INSTALL-BATCH size=2$' <<<"$output")" 'first install batch did not honor bound' || return 1
  assert_eq 1 "$(grep -c '^INSTALL-BATCH size=1$' <<<"$output")" 'remainder install batch is missing' || return 1
  assert_eq 2 "$(grep -c '^HEALTH-BATCH' <<<"$output")" 'unexpected health batch count'
}

tests=(
  test_successful_targeted_rollout
  test_dry_run_is_read_only
  test_checksum_failure_precedes_mutation
  test_partial_install_rolls_back_only_changed_targets
  test_health_failure_rolls_back_installed_targets
  test_concurrency_is_bounded_in_batches
)

failures=0
for test_name in "${tests[@]}"; do
  printf 'TEST %s\n' "$test_name"
  if "$test_name"; then
    printf '  PASS\n'
  else
    printf '  FAIL\n'
    ((failures += 1))
  fi
done

if ((failures)); then
  printf '%d test(s) failed\n' "$failures" >&2
  exit 1
fi
printf 'all %d tests passed\n' "${#tests[@]}"
