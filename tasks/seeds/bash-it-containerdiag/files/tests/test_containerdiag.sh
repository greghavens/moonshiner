#!/usr/bin/env bash
set -uo pipefail

export LC_ALL=C

test_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root_dir=$(CDPATH= cd -- "$test_dir/.." && pwd)
ctl=(bash "$root_dir/bin/containerctl")
failures=0

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  failures=$((failures + 1))
}

expect_contains() {
  local text=$1
  local needle=$2
  local label=$3
  if [[ $text != *"$needle"* ]]; then
    fail "$label (missing: $needle)"
  fi
}

if ! bash -n "$root_dir/bin/containerctl"; then
  fail 'containerctl must parse as Bash'
fi

expected_image_sha=c07a9ceb79b8c51c6838046ea079ebf38a94e38d1312d3f8bc7f7dd15a994b91
actual_image_sha=$(sha256sum "$root_dir/fixtures/image-inspect.json" | awk '{print $1}')
if [[ $actual_image_sha != "$expected_image_sha" ]]; then
  fail 'the local ledger-api image metadata must be preserved byte-for-byte'
fi

mapfile -t configured_ports < <(sed -n 's/^APP_PORT=//p' "$root_dir/deploy/ledger-api.env")
if [[ ${#configured_ports[@]} -ne 1 || ${configured_ports[0]-} != 8080 ]]; then
  fail 'APP_PORT must have one value matching the image healthcheck port 8080'
fi

image_output=$("${ctl[@]}" image inspect localhost/ledger-api:1.4 2>&1)
expect_contains "$image_output" '"Id": "sha256:73f4e150b6f124ab6a3f6baf9f0ebd5cdb206c2451aad6c35dd6630bf38fef75"' 'image identity changed'
expect_contains "$image_output" '"Entrypoint": ["/usr/local/bin/ledger-api"]' 'image entrypoint changed'
expect_contains "$image_output" '"Test": ["CMD", "/usr/local/bin/healthcheck", "--port", "8080"]' 'image healthcheck changed'

inspect_output=$("${ctl[@]}" inspect ledger-api 2>&1)
expect_contains "$inspect_output" '"Image": "sha256:73f4e150b6f124ab6a3f6baf9f0ebd5cdb206c2451aad6c35dd6630bf38fef75"' 'effective container image changed'
expect_contains "$inspect_output" '"CommandLine": "/usr/local/bin/ledger-api serve"' 'container command changed'
expect_contains "$inspect_output" '"APP_MODE=production", "APP_PORT=8080", "LOG_LEVEL=info"' 'effective container environment is not repaired'
expect_contains "$inspect_output" '"Binds": ["./data:/var/lib/ledger:rw"]' 'container mount changed'
expect_contains "$inspect_output" '"Memory": 268435456' 'memory limit changed'
expect_contains "$inspect_output" '"NanoCpus": 500000000' 'CPU limit changed'
expect_contains "$inspect_output" '"PidsLimit": 64' 'PID limit changed'
expect_contains "$inspect_output" '"Health": {"Status": "healthy", "ProbePort": 8080}' 'container did not become healthy'

if [[ ! -f $root_dir/data/ledger.db ]] || [[ $(<"$root_dir/data/ledger.db") != fixture-ledger-v1 ]]; then
  fail 'mounted ledger data must be preserved'
fi

logs_output=$("${ctl[@]}" logs ledger-api 2>&1)
expect_contains "$logs_output" 'msg="listening" address=0.0.0.0:8080' 'application is not listening on the health port'
expect_contains "$logs_output" 'msg="health probe passed" target=127.0.0.1:8080' 'logs do not show a passing probe'

diagnose_output=$("${ctl[@]}" diagnose ledger-api 2>&1)
for heading in IMAGE HEALTHCHECK COMMAND ENVIRONMENT MOUNT LIMITS LOG ASSESSMENT; do
  expect_contains "$diagnose_output" "$heading:" "diagnosis omitted $heading evidence"
done
expect_contains "$diagnose_output" 'ASSESSMENT: deployment port matches the image healthcheck' 'diagnosis still reports a port mismatch'

recreate_output=$("${ctl[@]}" recreate ledger-api 2>&1)
expect_contains "$recreate_output" 'image_id: sha256:73f4e150b6f124ab6a3f6baf9f0ebd5cdb206c2451aad6c35dd6630bf38fef75 (preserved)' 'recreate did not preserve the image'
expect_contains "$recreate_output" 'health: healthy' 'recreated container is not healthy'

health_status=0
health_output=$("${ctl[@]}" health ledger-api 2>&1) || health_status=$?
if [[ $health_status -ne 0 ]]; then
  fail "health command exited $health_status: ${health_output//$'\n'/; }"
fi
expect_contains "$health_output" 'ledger-api: healthy' 'health command did not report healthy'
expect_contains "$health_output" 'probe: tcp://127.0.0.1:8080' 'health probe target changed'
expect_contains "$health_output" 'listener: tcp://127.0.0.1:8080' 'application listener does not match probe'

if [[ $failures -ne 0 ]]; then
  printf '%d container diagnostic assertion(s) failed\n' "$failures" >&2
  exit 1
fi

printf '%s\n' 'all container diagnostic checks passed'
