#!/usr/bin/env bash
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runtime=$(mktemp -d "${TMPDIR:-/tmp}/systemd-repair.XXXXXX")
trap 'rm -rf -- "$runtime"' EXIT
export UNITCTL_STATE_DIR=$runtime/state
mkdir -p "$UNITCTL_STATE_DIR"

failures=0

fail() {
  printf 'not ok - %s\n' "$1" >&2
  failures=$((failures + 1))
}

assert_eq() {
  local expected=$1 actual=$2 message=$3
  if [[ $actual != "$expected" ]]; then
    fail "$message (expected '$expected', got '$actual')"
  fi
}

assert_file_line() {
  local line=$1 path=$2 message=$3
  if ! grep -Fqx -- "$line" "$path"; then
    fail "$message (missing '$line')"
  fi
}

configured_path=$(bash -c '
  source "$1"
  printf "%s" "${CATALOG_IMPORT_CONFIG-}"
' bash "$root/environment/catalog-import.env")
assert_eq config/catalog-import.production.conf "$configured_path" \
  'operator environment should select the deployed production configuration'

# Simulate the unrelated unit being active before this repair is exercised.
printf 'active\n' >"$UNITCTL_STATE_DIR/telemetry-sidecar.service.state"
printf '7\n' >"$UNITCTL_STATE_DIR/telemetry-sidecar.service.starts"
telemetry_before=$(cksum \
  "$UNITCTL_STATE_DIR/telemetry-sidecar.service.state" \
  "$UNITCTL_STATE_DIR/telemetry-sidecar.service.starts")

start_stdout=$runtime/start.stdout
start_stderr=$runtime/start.stderr
if "$root/bin/unitctl" start catalog-import.service >"$start_stdout" 2>"$start_stderr"; then
  printf 'ok - catalog-import.service starts\n'
else
  rc=$?
  sed -n '1,20p' "$start_stderr" >&2
  fail "catalog-import.service start failed with status $rc"
fi

assert_eq active "$("$root/bin/unitctl" is-active catalog-import.service 2>/dev/null)" \
  'catalog-import.service should be active after start'
assert_eq active "$("$root/bin/unitctl" is-active catalog-db.service 2>/dev/null)" \
  'required catalog-db.service should be active'
assert_eq active "$("$root/bin/unitctl" is-active network-online.target 2>/dev/null)" \
  'wanted network-online.target should be active'
assert_eq 1 "$("$root/bin/unitctl" starts catalog-db.service)" \
  'catalog-db.service should start exactly once'
assert_eq 1 "$("$root/bin/unitctl" starts network-online.target)" \
  'network-online.target should start exactly once'

receipt=$UNITCTL_STATE_DIR/output/catalog-import.receipt
if [[ ! -f $receipt ]]; then
  fail 'successful start should write an import receipt'
else
  assert_file_line 'service=catalog-import.service' "$receipt" 'receipt should identify the service'
  assert_file_line 'profile=production' "$receipt" 'receipt should use production configuration'
  assert_file_line 'source=data/pending-catalog.tsv' "$receipt" 'receipt should identify its source'
  assert_file_line 'records=3' "$receipt" 'receipt should contain the imported record count'
  assert_file_line 'start=1' "$receipt" 'first activation should be recorded'
fi

restart_stderr=$runtime/restart.stderr
if "$root/bin/unitctl" restart catalog-import.service >/dev/null 2>"$restart_stderr"; then
  printf 'ok - catalog-import.service restarts\n'
else
  rc=$?
  sed -n '1,20p' "$restart_stderr" >&2
  fail "catalog-import.service restart failed with status $rc"
fi

assert_eq active "$("$root/bin/unitctl" is-active catalog-import.service 2>/dev/null)" \
  'catalog-import.service should be active after restart'
assert_eq active "$("$root/bin/unitctl" is-active catalog-db.service 2>/dev/null)" \
  'required catalog-db.service should remain active after restart'
assert_eq active "$("$root/bin/unitctl" is-active network-online.target 2>/dev/null)" \
  'wanted network-online.target should remain active after restart'
assert_eq 2 "$("$root/bin/unitctl" starts catalog-import.service)" \
  'catalog-import.service should have two successful activations'
assert_eq 1 "$("$root/bin/unitctl" starts catalog-db.service)" \
  'restart should not redundantly restart catalog-db.service'
assert_eq 1 "$("$root/bin/unitctl" starts network-online.target)" \
  'restart should not redundantly restart network-online.target'
if [[ -f $receipt ]]; then
  assert_file_line 'start=2' "$receipt" 'restart should replace the receipt with the second activation'
fi

telemetry_after=$(cksum \
  "$UNITCTL_STATE_DIR/telemetry-sidecar.service.state" \
  "$UNITCTL_STATE_DIR/telemetry-sidecar.service.starts")
assert_eq "$telemetry_before" "$telemetry_after" \
  'unrelated telemetry-sidecar.service state must remain byte-for-byte unchanged'
assert_eq active "$("$root/bin/unitctl" is-active telemetry-sidecar.service 2>/dev/null)" \
  'unrelated telemetry-sidecar.service should remain active'
assert_eq 7 "$("$root/bin/unitctl" starts telemetry-sidecar.service)" \
  'unrelated telemetry-sidecar.service must not be restarted'

if ((failures)); then
  printf 'FAILED: %d assertion(s)\n' "$failures" >&2
  exit 1
fi

printf 'PASS: start, restart, dependencies, receipt, and unrelated-unit isolation verified\n'
