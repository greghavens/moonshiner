#!/usr/bin/env bash

set -u
set -o pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
restore=$project_root/restore-files.sh
fixture=$project_root/fixtures/backup
test_root=$(mktemp -d "${TMPDIR:-/tmp}/filerestore-tests.XXXXXX") || exit 1
trap 'rm -rf -- "$test_root"' EXIT HUP INT TERM

passed=0
failed=0

pass() {
    passed=$((passed + 1))
    printf 'ok %d - %s\n' "$((passed + failed))" "$1"
}

fail_test() {
    failed=$((failed + 1))
    printf 'not ok %d - %s\n' "$((passed + failed))" "$1"
}

assert_no_artifacts() {
    local directory=$1
    ! find "$directory" -mindepth 1 -maxdepth 1 \
        \( -name '.filerestore-stage.*' -o -name '.filerestore-rollback.*' \) \
        -print -quit | grep -q .
}

test_selected_contents_and_metadata() {
    local destination=$test_root/selected
    local unrelated_mode unrelated_mtime
    mkdir -p "$destination"
    printf 'leave me alone\n' > "$destination/unrelated.txt"
    chmod 640 "$destination/unrelated.txt"
    touch -d '@946684800' "$destination/unrelated.txt"
    unrelated_mode=$(stat -c '%a' "$destination/unrelated.txt")
    unrelated_mtime=$(stat -c '%Y' "$destination/unrelated.txt")

    bash "$restore" "$fixture" "$destination" config/app.conf bin/greet.sh || return 1
    cmp "$fixture/payload/config/app.conf" "$destination/config/app.conf" || return 1
    cmp "$fixture/payload/bin/greet.sh" "$destination/bin/greet.sh" || return 1
    [[ ! -e $destination/docs/message.txt ]] || return 1
    [[ $(stat -c '%a' "$fixture/payload/bin/greet.sh") == \
       $(stat -c '%a' "$destination/bin/greet.sh") ]] || return 1
    [[ $(stat -c '%Y' "$fixture/payload/config/app.conf") == \
       $(stat -c '%Y' "$destination/config/app.conf") ]] || return 1
    [[ $(<"$destination/unrelated.txt") == 'leave me alone' ]] || return 1
    [[ $(stat -c '%a' "$destination/unrelated.txt") == "$unrelated_mode" ]] || return 1
    [[ $(stat -c '%Y' "$destination/unrelated.txt") == "$unrelated_mtime" ]] || return 1
    assert_no_artifacts "$destination"
}

test_overwrite_policy() {
    local destination=$test_root/overwrite
    mkdir -p "$destination/config"
    printf 'local value\n' > "$destination/config/app.conf"
    if bash "$restore" "$fixture" "$destination" config/app.conf >/dev/null 2>&1; then
        return 1
    fi
    [[ $(<"$destination/config/app.conf") == 'local value' ]] || return 1
    bash "$restore" --overwrite "$fixture" "$destination" config/app.conf || return 1
    cmp "$fixture/payload/config/app.conf" "$destination/config/app.conf" || return 1
    assert_no_artifacts "$destination"
}

test_checksum_failure_is_non_mutating() {
    local backup=$test_root/bad-backup
    local destination=$test_root/checksum
    cp -a "$fixture" "$backup"
    mkdir -p "$destination"
    printf 'corrupted\n' > "$backup/payload/docs/message.txt"
    printf 'sentinel\n' > "$destination/sentinel"
    if bash "$restore" "$backup" "$destination" docs/message.txt >/dev/null 2>&1; then
        return 1
    fi
    [[ ! -e $destination/docs/message.txt ]] || return 1
    [[ $(<"$destination/sentinel") == sentinel ]] || return 1
    assert_no_artifacts "$destination"
}

test_unsafe_paths_and_symlinks() {
    local destination=$test_root/safety/destination
    local outside=$test_root/safety/outside
    mkdir -p "$destination" "$outside"
    if bash "$restore" "$fixture" "$destination" ../config/app.conf >/dev/null 2>&1; then
        return 1
    fi
    if bash "$restore" "$fixture" "$destination" /etc/passwd >/dev/null 2>&1; then
        return 1
    fi
    ln -s "$outside" "$destination/config"
    if bash "$restore" "$fixture" "$destination" config/app.conf >/dev/null 2>&1; then
        return 1
    fi
    [[ ! -e $outside/app.conf ]] || return 1
    assert_no_artifacts "$destination"
}

test_nested_overwrite_rolls_back() {
    local destination=$test_root/rollback
    local before_hash
    mkdir -p "$destination/config"
    printf 'original destination value\n' > "$destination/config/app.conf"
    chmod 600 "$destination/config/app.conf"
    touch -d '@978307200' "$destination/config/app.conf"
    printf 'unrelated\n' > "$destination/unrelated"
    printf 'parent blocker\n' > "$destination/blocked"
    before_hash=$(sha256sum "$destination/config/app.conf")

    if bash "$restore" --overwrite "$fixture" "$destination" \
        docs/message.txt config/app.conf blocked/entry.txt >/dev/null 2>&1; then
        return 1
    fi
    [[ -f $destination/config/app.conf ]] || return 1
    [[ $(sha256sum "$destination/config/app.conf") == "$before_hash" ]] || return 1
    [[ $(stat -c '%a' "$destination/config/app.conf") == 600 ]] || return 1
    [[ $(stat -c '%Y' "$destination/config/app.conf") == 978307200 ]] || return 1
    [[ $(<"$destination/unrelated") == unrelated ]] || return 1
    [[ $(<"$destination/blocked") == 'parent blocker' ]] || return 1
    [[ ! -e $destination/docs ]] || return 1
    [[ ! -e $destination/app.conf ]] || return 1
    assert_no_artifacts "$destination"
}

run_test() {
    local name=$1
    local function_name=$2
    if "$function_name"; then
        pass "$name"
    else
        fail_test "$name"
    fi
}

printf 'TAP version 13\n'
run_test 'selected contents and metadata are restored' test_selected_contents_and_metadata
run_test 'overwrite policy is enforced' test_overwrite_policy
run_test 'checksum failure does not mutate the destination' test_checksum_failure_is_non_mutating
run_test 'unsafe paths and symlink traversal are rejected' test_unsafe_paths_and_symlinks
run_test 'nested overwrites are restored after a later failure' test_nested_overwrite_rolls_back
printf '1..%d\n' "$((passed + failed))"

[[ $failed -eq 0 ]]
