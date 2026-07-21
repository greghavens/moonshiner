#!/usr/bin/env bash

set -u

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PROBE="$PROJECT_ROOT/bin/readiness-probe"
FAKE_BIN="$PROJECT_ROOT/tests/fake-bin"
TEST_TMP=$(mktemp -d "${TMPDIR:-/tmp}/readiness-probe-tests.XXXXXX") || exit 1
trap 'rm -rf -- "$TEST_TMP"' EXIT HUP INT TERM

PATH="$FAKE_BIN:/usr/bin:/bin"
export PATH
export READINESS_TEST_AVAILABLE_KB=8192

NOW=2000000000
BACKUP_EPOCH=1999999900
tests_run=0
tests_failed=0
FIXTURE=
OUTPUT=
STATUS=0

new_fixture() {
  local name=$1

  FIXTURE="$TEST_TMP/$name"
  mkdir -p -- "$FIXTURE/capacity"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$FIXTURE/dependency"
  chmod 0755 "$FIXTURE/dependency"
  printf 'backup payload\n' > "$FIXTURE/backup.tar"
  touch -d "@$BACKUP_EPOCH" "$FIXTURE/backup.tar"
  printf 'healthy\n' > "$FIXTURE/monitor.status"
  printf 'granted\n' > "$FIXTURE/access.status"
  printf 'rollback payload\n' > "$FIXTURE/rollback.tar"
}

run_probe() {
  set +e
  OUTPUT=$(
    "$PROBE" \
      --dependency "$FIXTURE/dependency" \
      --capacity-path "$FIXTURE/capacity" \
      --min-free-kb 4096 \
      --backup-file "$FIXTURE/backup.tar" \
      --now "$NOW" \
      --max-backup-age 3600 \
      --monitor-file "$FIXTURE/monitor.status" \
      --access-file "$FIXTURE/access.status" \
      --rollback-file "$FIXTURE/rollback.tar" 2>&1
  )
  STATUS=$?
  set -e
}

assert_result() {
  local name=$1
  local expected_status=$2
  local expected_output=$3

  tests_run=$((tests_run + 1))
  if [[ $STATUS -ne $expected_status || $OUTPUT != "$expected_output" ]]; then
    tests_failed=$((tests_failed + 1))
    printf 'not ok %d - %s\n' "$tests_run" "$name"
    printf '  expected status: %s\n' "$expected_status"
    printf '  actual status:   %s\n' "$STATUS"
    printf '  expected output:\n%s\n' "$expected_output"
    printf '  actual output:\n%s\n' "$OUTPUT"
  else
    printf 'ok %d - %s\n' "$tests_run" "$name"
  fi
}

snapshot_fixture() {
  local root=$1
  local path relative digest

  while IFS= read -r path; do
    relative=${path#"$root"/}
    if [[ -f $path ]]; then
      digest=$(sha256sum -- "$path")
      digest=${digest%% *}
      printf '%s|file|%s|%s|%s\n' \
        "$relative" "$(stat -c '%a' -- "$path")" \
        "$(stat -c '%Y' -- "$path")" "$digest"
    elif [[ -d $path ]]; then
      printf '%s|directory|%s|%s\n' \
        "$relative" "$(stat -c '%a' -- "$path")" \
        "$(stat -c '%Y' -- "$path")"
    fi
  done < <(find "$root" -mindepth 1 -print | LC_ALL=C sort)
}

new_fixture ready
run_probe
assert_result 'all checks ready' 0 'READY'

new_fixture dependency
chmod 0644 "$FIXTURE/dependency"
run_probe
assert_result 'dependency must be executable' 1 \
  "FAIL dependency: not an executable file: $FIXTURE/dependency"

new_fixture capacity
READINESS_TEST_AVAILABLE_KB=2048
export READINESS_TEST_AVAILABLE_KB
run_probe
assert_result 'capacity threshold is enforced' 1 \
  'FAIL capacity: 2048 KB available; requires 4096 KB'
READINESS_TEST_AVAILABLE_KB=8192
export READINESS_TEST_AVAILABLE_KB

new_fixture backup_missing
rm -- "$FIXTURE/backup.tar"
run_probe
assert_result 'backup artifact is required' 1 \
  "FAIL backup: missing or empty artifact: $FIXTURE/backup.tar"

new_fixture backup_stale
touch -d '@1999990000' "$FIXTURE/backup.tar"
run_probe
assert_result 'backup freshness is enforced' 1 \
  'FAIL backup: artifact is 10000 seconds old; maximum is 3600'

new_fixture monitoring
printf 'degraded\n' > "$FIXTURE/monitor.status"
run_probe
assert_result 'monitoring must be healthy' 1 \
  "FAIL monitoring: status is 'degraded'; expected 'healthy'"

new_fixture access
printf 'denied\n' > "$FIXTURE/access.status"
run_probe
assert_result 'maintenance access must be granted' 1 \
  "FAIL access: status is 'denied'; expected 'granted'"

new_fixture rollback
rm -- "$FIXTURE/rollback.tar"
run_probe
assert_result 'rollback artifact is required independently' 1 \
  "FAIL rollback: missing or empty artifact: $FIXTURE/rollback.tar"

new_fixture rollback_empty
: > "$FIXTURE/rollback.tar"
run_probe
assert_result 'rollback artifact must be non-empty' 1 \
  "FAIL rollback: missing or empty artifact: $FIXTURE/rollback.tar"

new_fixture ordered_failures
chmod 0644 "$FIXTURE/dependency"
printf 'degraded\n' > "$FIXTURE/monitor.status"
printf 'denied\n' > "$FIXTURE/access.status"
rm -- "$FIXTURE/rollback.tar"
READINESS_TEST_AVAILABLE_KB=2048
export READINESS_TEST_AVAILABLE_KB
run_probe
assert_result 'all failures are reported in stable check order' 1 \
  "FAIL dependency: not an executable file: $FIXTURE/dependency
FAIL capacity: 2048 KB available; requires 4096 KB
FAIL monitoring: status is 'degraded'; expected 'healthy'
FAIL access: status is 'denied'; expected 'granted'
FAIL rollback: missing or empty artifact: $FIXTURE/rollback.tar"
READINESS_TEST_AVAILABLE_KB=8192
export READINESS_TEST_AVAILABLE_KB

new_fixture read_only
before=$(snapshot_fixture "$FIXTURE")
run_probe
after=$(snapshot_fixture "$FIXTURE")
if [[ $before != "$after" ]]; then
  STATUS=99
  OUTPUT='fixture contents or metadata changed'
fi
assert_result 'probe makes no filesystem changes' 0 'READY'

new_fixture invalid_number
set +e
OUTPUT=$(
  "$PROBE" \
    --dependency "$FIXTURE/dependency" \
    --capacity-path "$FIXTURE/capacity" \
    --min-free-kb '4k' \
    --backup-file "$FIXTURE/backup.tar" \
    --now "$NOW" \
    --max-backup-age 3600 \
    --monitor-file "$FIXTURE/monitor.status" \
    --access-file "$FIXTURE/access.status" \
    --rollback-file "$FIXTURE/rollback.tar" 2>&1
)
STATUS=$?
set -e
assert_result 'invalid numeric input is rejected before checks' 2 \
  'readiness-probe: MIN_FREE_KB must be a non-negative integer'

printf '1..%d\n' "$tests_run"
if (( tests_failed > 0 )); then
  printf '%d test(s) failed\n' "$tests_failed" >&2
  exit 1
fi

printf 'all %d tests passed\n' "$tests_run"
