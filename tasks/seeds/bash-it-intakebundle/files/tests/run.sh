#!/usr/bin/env bash

set -u
set -o pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
program=$project_root/bin/intake-bundle
scratch=$(mktemp -d "${TMPDIR:-/tmp}/intake-bundle-tests.XXXXXX") || exit 1
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM

passed=0
failed=0

new_case() {
    case_root=$scratch/$1
    source_dir=$case_root/source
    bundle=$case_root/bundle.tar.gz
    extracted=$case_root/extracted
    mkdir -p -- "$source_dir"
    cp -R -- "$project_root/fixtures/." "$source_dir/"
}

run_bundle() {
    "$program" --source "$source_dir" --output "$bundle"
}

extract_bundle() {
    mkdir -p -- "$extracted"
    tar -xzf "$bundle" -C "$extracted"
}

assert_contains() {
    local needle=$1 path=$2
    if ! grep -Fq -- "$needle" "$path"; then
        printf 'expected %q in %s\n' "$needle" "$path" >&2
        return 1
    fi
}

assert_not_contains() {
    local needle=$1 path=$2
    if grep -Fq -- "$needle" "$path"; then
        printf 'did not expect %q in %s\n' "$needle" "$path" >&2
        return 1
    fi
}

run_test() {
    local name=$1
    shift
    if ("$@"); then
        printf 'ok - %s\n' "$name"
        ((passed += 1))
    else
        printf 'not ok - %s\n' "$name"
        ((failed += 1))
    fi
}

test_allowlist_manifest_and_optional_skip() {
    new_case allowlist
    run_bundle > "$case_root/stdout" 2> "$case_root/stderr" || return 1
    extract_bundle || return 1

    actual=$case_root/actual-files
    expected=$case_root/expected-files
    find "$extracted" -type f -printf '%P\n' | LC_ALL=C sort > "$actual"
    cat > "$expected" <<'EOF'
evidence/config/bash-it.env
evidence/logs/application.log
evidence/system/disk.txt
evidence/versions/bash-it.txt
manifest.tsv
EOF
    diff -u "$expected" "$actual" || return 1
    [[ ! -e $extracted/evidence/notes/private.txt ]] || return 1
    grep -Fxq $'skipped\tlog\tlogs/worker.log\tmissing_optional' \
        "$extracted/manifest.tsv" || return 1

    while IFS=$'\t' read -r status kind relative value; do
        [[ $status == status ]] && continue
        if [[ $status == collected ]]; then
            calculated=$(sha256sum "$extracted/evidence/$relative") || return 1
            calculated=${calculated%% *}
            [[ $value == "$calculated" ]] || return 1
        fi
    done < "$extracted/manifest.tsv"
}

test_configuration_is_redacted() {
    new_case redaction
    run_bundle > /dev/null 2> "$case_root/stderr" || return 1
    extract_bundle || return 1
    config=$extracted/evidence/config/bash-it.env

    assert_contains 'SERVICE_MODE=production' "$config" &&
        assert_contains 'API_TOKEN=[REDACTED]' "$config" &&
        assert_contains 'DB_PASSWORD=[REDACTED]' "$config" &&
        assert_contains 'PUBLIC_ENDPOINT=http://127.0.0.1:8080' "$config" &&
        assert_not_contains 'fixture-api-token-must-not-leak' "$config" &&
        assert_not_contains 'fixture-db-password-must-not-leak' "$config"
}

test_archive_is_byte_deterministic() {
    new_case deterministic
    first=$case_root/first.tar.gz
    second=$case_root/second.tar.gz

    bundle=$first
    (umask 022; run_bundle > /dev/null 2> "$case_root/first.stderr") || return 1
    touch -t 203712312359 "$source_dir/logs/application.log" \
        "$source_dir/versions/bash-it.txt" "$source_dir/system/disk.txt" \
        "$source_dir/config/bash-it.env"
    chmod 0600 "$source_dir/logs/application.log" \
        "$source_dir/versions/bash-it.txt" "$source_dir/system/disk.txt" \
        "$source_dir/config/bash-it.env"
    bundle=$second
    (umask 077; run_bundle > /dev/null 2> "$case_root/second.stderr") || return 1

    cmp -s -- "$first" "$second"
}

test_missing_required_evidence_is_atomic() {
    new_case required
    rm -f -- "$source_dir/system/disk.txt"
    printf 'PREEXISTING OUTPUT\n' > "$bundle"
    before=$(sha256sum "$bundle")
    status=0
    run_bundle > "$case_root/stdout" 2> "$case_root/stderr" || status=$?
    after=$(sha256sum "$bundle")

    [[ $status -ne 0 ]] &&
        [[ $before == "$after" ]] &&
        assert_contains 'required evidence is missing: system/disk.txt' "$case_root/stderr"
}

test_internal_symlink_is_collected() {
    new_case internal
    mkdir -p -- "$source_dir/shared"
    mv -- "$source_dir/logs/application.log" "$source_dir/shared/application.log"
    ln -s ../shared/application.log "$source_dir/logs/application.log"
    run_bundle > /dev/null 2> "$case_root/stderr" || return 1
    extract_bundle || return 1

    cmp -s -- "$source_dir/shared/application.log" \
        "$extracted/evidence/logs/application.log"
}

test_unrelated_external_symlink_is_rejected() {
    new_case external
    outside=$case_root/private
    mkdir -p -- "$outside"
    printf 'OUTSIDE SECRET\n' > "$outside/application.log"
    rm -f -- "$source_dir/logs/application.log"
    ln -s "$outside/application.log" "$source_dir/logs/application.log"
    status=0
    run_bundle > "$case_root/stdout" 2> "$case_root/stderr" || status=$?

    [[ $status -ne 0 ]] &&
        [[ ! -e $bundle ]] &&
        assert_contains 'allowlisted path escapes source root: logs/application.log' \
            "$case_root/stderr"
}

test_sibling_prefix_symlink_is_rejected_atomically() {
    new_case prefix-escape
    outside=$case_root/source-private
    mkdir -p -- "$outside"
    printf 'SIBLING PREFIX SECRET MUST NOT BE COLLECTED\n' > "$outside/application.log"
    rm -f -- "$source_dir/logs/application.log"
    ln -s "$outside/application.log" "$source_dir/logs/application.log"
    printf 'PREEXISTING OUTPUT\n' > "$bundle"
    before=$(sha256sum "$bundle")
    status=0
    run_bundle > "$case_root/stdout" 2> "$case_root/stderr" || status=$?
    after=$(sha256sum "$bundle")

    [[ $status -ne 0 ]] &&
        [[ $before == "$after" ]] &&
        assert_contains 'allowlisted path escapes source root: logs/application.log' \
            "$case_root/stderr"
}

run_test 'allowlist, manifest hashes, and optional skip are complete' \
    test_allowlist_manifest_and_optional_skip
run_test 'configuration secrets are redacted' test_configuration_is_redacted
run_test 'archive bytes are deterministic' test_archive_is_byte_deterministic
run_test 'missing required evidence preserves existing output' \
    test_missing_required_evidence_is_atomic
run_test 'internal symlinks remain collectable' test_internal_symlink_is_collected
run_test 'ordinary external symlinks are rejected' \
    test_unrelated_external_symlink_is_rejected
run_test 'sibling-prefix symlink escapes are rejected atomically' \
    test_sibling_prefix_symlink_is_rejected_atomically

printf '%d passed; %d failed\n' "$passed" "$failed"
((failed == 0))
