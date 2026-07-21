#!/usr/bin/env bash
set -u
export LC_ALL=C

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_tmp=$(mktemp -d)
failures=0
checks=0

cleanup() {
    rm -rf -- "$test_tmp"
}
trap cleanup EXIT HUP INT TERM

fail() {
    printf 'not ok %s - %s\n' "$checks" "$1" >&2
    failures=$((failures + 1))
}

pass() {
    printf 'ok %s - %s\n' "$checks" "$1"
}

assert_contains() {
    local output=$1 expected=$2 label=$3
    checks=$((checks + 1))
    if grep -Fqx -- "$expected" <<< "$output"; then
        pass "$label"
    else
        fail "$label (missing: $expected)"
    fi
}

assert_status() {
    local actual=$1 expected=$2 label=$3
    checks=$((checks + 1))
    if [[ "$actual" == "$expected" ]]; then
        pass "$label"
    else
        fail "$label (expected status $expected, got $actual)"
    fi
}

assert_exists() {
    local scenario=$1 path=$2 label=$3
    checks=$((checks + 1))
    if bash "$repo/bin/diskfixture" "$scenario" exists "$path"; then
        pass "$label"
    else
        fail "$label"
    fi
}

assert_absent() {
    local scenario=$1 path=$2 label=$3
    checks=$((checks + 1))
    if bash "$repo/bin/diskfixture" "$scenario" exists "$path"; then
        fail "$label"
    else
        pass "$label"
    fi
}

assert_file_equals() {
    local expected=$1 actual=$2 label=$3
    checks=$((checks + 1))
    if cmp -s -- "$expected" "$actual"; then
        pass "$label"
    else
        fail "$label"
        diff -u -- "$expected" "$actual" >&2 || true
    fi
}

checks=$((checks + 1))
if bash -n "$repo/bin/diskpressure" "$repo/bin/diskfixture"; then
    pass 'scripts parse as Bash'
else
    fail 'scripts parse as Bash'
fi

block_output=$(bash "$repo/bin/diskpressure" diagnose "$repo/fixtures/block-pressure" 2>&1)
block_status=$?
assert_status "$block_status" 0 'block fixture is diagnosable'
assert_contains "$block_output" 'pressure=blocks' 'high block use is classified as block pressure'
assert_contains "$block_output" 'blocks_percent=95' 'block utilization comes from the deterministic df fixture'
assert_contains "$block_output" 'inodes_percent=41' 'healthy inode headroom is reported separately'
assert_contains "$block_output" 'largest_path=/var/log' 'largest du entry is correlated'
assert_contains "$block_output" 'largest_bytes=62914560' 'largest du byte count is preserved'
assert_contains "$block_output" 'log_enospc_events=2' 'captured ENOSPC log events are counted'

inode_output=$(bash "$repo/bin/diskpressure" diagnose "$repo/fixtures/inode-pressure" 2>&1)
inode_status=$?
assert_status "$inode_status" 0 'inode fixture is diagnosable'
assert_contains "$inode_output" 'pressure=inodes' 'high inode use is distinguished from block pressure'
assert_contains "$inode_output" 'blocks_percent=60' 'inode incident retains block capacity'
assert_contains "$inode_output" 'inodes_percent=98' 'inode exhaustion is reported'
assert_contains "$inode_output" 'largest_bytes=8388608' 'inode diagnosis retains the modest du total'
assert_contains "$inode_output" 'log_enospc_events=1' 'inode incident log evidence is reported'

cp -R -- "$repo/fixtures/block-pressure" "$test_tmp/block-pressure"
audit=$test_tmp/deletions.tsv
remediation_output=$(bash "$repo/bin/diskpressure" remediate "$test_tmp/block-pressure" "$audit" 2>&1)
remediation_status=$?
assert_status "$remediation_status" 0 'approved block cleanup completes'

assert_absent "$test_tmp/block-pressure" '/var/cache/acme/chunks/chunk-a.bin' 'first approved cache file is removed'
assert_absent "$test_tmp/block-pressure" '/var/cache/acme/chunks/chunk-b.bin' 'second approved cache file is removed'
assert_absent "$test_tmp/block-pressure" '/var/cache/standalone.bin' 'exactly approved cache file is removed'
assert_exists "$test_tmp/block-pressure" '/var/cache/acme-old/do-not-delete.bin' 'similarly named unapproved cache remains'

checks=$((checks + 1))
if [[ -f "$test_tmp/block-pressure/fs/var/cache/acme/index/keep.idx" ]]; then
    pass 'non-candidate data within the approved cache remains'
else
    fail 'non-candidate data within the approved cache remains'
fi

checks=$((checks + 1))
if [[ -f "$test_tmp/block-pressure/fs/var/log/workstation.log" ]]; then
    pass 'log data remains untouched'
else
    fail 'log data remains untouched'
fi

printf 'path\tbytes\n/var/cache/acme/chunks/chunk-a.bin\t3145728\n/var/cache/acme/chunks/chunk-b.bin\t4194304\n/var/cache/standalone.bin\t1048576\n' > "$test_tmp/expected-audit.tsv"
assert_file_equals "$test_tmp/expected-audit.tsv" "$audit" 'audit records every and only approved deletion with byte counts'
assert_contains "$remediation_output" 'pressure=healthy' 'post-cleanup metrics are diagnosed again'
assert_contains "$remediation_output" 'blocks_used=86808' 'observed used blocks reflect approved deletions only'
assert_contains "$remediation_output" 'blocks_available=13192' 'available capacity is verified after cleanup'
assert_contains "$remediation_output" 'deleted_paths=3' 'deletion count is reported'
assert_contains "$remediation_output" 'skipped_paths=1' 'unapproved candidate is reported as skipped'
assert_contains "$remediation_output" 'reclaimed_bytes=8388608' 'reclaimed byte count is exact'
assert_contains "$remediation_output" 'reclaimed_blocks=8192' 'reclaimed block capacity is verified'
assert_contains "$remediation_output" 'reclaimed_inodes=3' 'reclaimed inode count is verified'

cp -R -- "$repo/fixtures/inode-pressure" "$test_tmp/inode-pressure"
inode_audit=$test_tmp/inode-audit.tsv
inode_remediation=$(bash "$repo/bin/diskpressure" remediate "$test_tmp/inode-pressure" "$inode_audit" 2>&1)
inode_remediation_status=$?
assert_status "$inode_remediation_status" 3 'block cleanup is refused for inode exhaustion'
assert_contains "$inode_remediation" 'diskpressure: refusing cleanup for pressure=inodes' 'inode refusal explains the diagnosis'
assert_exists "$test_tmp/inode-pressure" '/home/alex/.cache/thumbs/index.db' 'inode-pressure data is not mutated'

checks=$((checks + 1))
if [[ ! -e "$inode_audit" ]]; then
    pass 'refused remediation creates no audit artifact'
else
    fail 'refused remediation creates no audit artifact'
fi

if (( failures > 0 )); then
    printf '%s of %s checks failed\n' "$failures" "$checks" >&2
    exit 1
fi
printf 'all %s checks passed\n' "$checks"
