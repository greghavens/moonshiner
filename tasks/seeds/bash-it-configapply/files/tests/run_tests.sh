#!/usr/bin/env bash

set -u
set -o pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
program=$project_root/bin/configapply
scratch=$(mktemp -d "${TMPDIR:-/tmp}/configapply-tests.XXXXXX") || exit 1
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM

passed=0
failed=0

new_case() {
    case_root=$scratch/$1
    mkdir -p "$case_root/templates" "$case_root/validators" "$case_root/managed" \
             "$case_root/backups" "$case_root/state"
    inventory=$case_root/inventory.tsv
    journal=$case_root/state/rollback.tsv
}

run_apply() {
    "$program" \
        --inventory "$inventory" \
        --root "$case_root/managed" \
        --templates "$case_root/templates" \
        --validators "$case_root/validators" \
        --backup-dir "$case_root/backups" \
        --journal "$journal" "$@"
}

assert_file_content() {
    local expected=$1 path=$2 actual
    if [[ ! -f $path ]]; then
        printf 'expected file is missing: %s\n' "$path" >&2
        return 1
    fi
    actual=$(<"$path")
    if [[ $actual != "$expected" ]]; then
        printf 'unexpected content in %s\nexpected: %q\nactual:   %q\n' \
            "$path" "$expected" "$actual" >&2
        return 1
    fi
}

assert_no_path() {
    if [[ -e $1 ]]; then
        printf 'path should not exist: %s\n' "$1" >&2
        return 1
    fi
}

assert_contains() {
    local needle=$1 path=$2
    if ! grep -Fq -- "$needle" "$path"; then
        printf 'expected %q in %s\n' "$needle" "$path" >&2
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

test_exact_host_selection_preserves_untargeted_host() {
    new_case exact-host
    printf 'web1\tweb\tetc/app.conf\tapp.conf\t-\nweb10\tweb\tetc/app.conf\tapp.conf\t-\n' > "$inventory"
    printf 'owner={{HOST}}\n' > "$case_root/templates/app.conf"
    mkdir -p "$case_root/managed/web1/etc" "$case_root/managed/web10/etc"
    printf 'owner=old-web1\n' > "$case_root/managed/web1/etc/app.conf"
    printf 'owner=do-not-touch\n' > "$case_root/managed/web10/etc/app.conf"

    run_apply --host web1 > "$case_root/output"

    assert_file_content 'owner=web1' "$case_root/managed/web1/etc/app.conf" &&
        assert_file_content 'owner=do-not-touch' "$case_root/managed/web10/etc/app.conf" &&
        assert_no_path "$case_root/backups/web10" &&
        ! grep -Fq $'web10\t' "$journal"
}

test_group_selection_is_exact() {
    new_case group
    printf 'api1\tapi,prod\tetc/app.conf\tapp.conf\t-\napi2\tapi-canary\tetc/app.conf\tapp.conf\t-\n' > "$inventory"
    printf 'host={{HOST}}\n' > "$case_root/templates/app.conf"
    mkdir -p "$case_root/managed/api1/etc" "$case_root/managed/api2/etc"
    printf 'old-one\n' > "$case_root/managed/api1/etc/app.conf"
    printf 'old-two\n' > "$case_root/managed/api2/etc/app.conf"

    run_apply --group api > "$case_root/output"

    assert_file_content 'host=api1' "$case_root/managed/api1/etc/app.conf" &&
        assert_file_content 'old-two' "$case_root/managed/api2/etc/app.conf"
}

test_check_mode_is_non_mutating() {
    new_case check
    printf 'web1\tweb\tetc/app.conf\tapp.conf\tvalid\n' > "$inventory"
    printf 'new={{HOST}}\n' > "$case_root/templates/app.conf"
    mkdir -p "$case_root/managed/web1/etc"
    printf 'old\n' > "$case_root/managed/web1/etc/app.conf"
    cat > "$case_root/validators/valid" <<'EOF'
#!/usr/bin/env bash
grep -Fq 'new=web1' "$2"
EOF
    chmod +x "$case_root/validators/valid"

    run_apply --check --host web1 > "$case_root/output"

    assert_file_content old "$case_root/managed/web1/etc/app.conf" &&
        assert_no_path "$journal" &&
        assert_no_path "$case_root/backups/web1" &&
        assert_contains $'WOULD_CHANGE\tweb1\tetc/app.conf' "$case_root/output"
}

test_change_has_backup_and_journal() {
    new_case backup
    printf 'db1\tdb\tetc/db.conf\tdb.conf\t-\n' > "$inventory"
    printf 'version=2\n' > "$case_root/templates/db.conf"
    mkdir -p "$case_root/managed/db1/etc"
    printf 'version=1\n' > "$case_root/managed/db1/etc/db.conf"

    run_apply > "$case_root/output"

    assert_file_content version=2 "$case_root/managed/db1/etc/db.conf" &&
        assert_file_content version=1 "$case_root/backups/db1/etc/db.conf" &&
        assert_contains $'PENDING\tdb1\tetc/db.conf\t' "$journal" &&
        assert_contains $'COMMITTED\tdb1\tetc/db.conf\t' "$journal" &&
        assert_contains $'CHANGED\tdb1\tetc/db.conf' "$case_root/output" &&
        ! find "$case_root/managed" -name '.configapply.*' -print -quit | grep -q .
}

test_validation_failure_rolls_back_and_continues() {
    new_case partial
    printf 'bad1\tapp\tetc/app.conf\tbad.conf\tvalidate\ngood1\tapp\tetc/app.conf\tgood.conf\tvalidate\n' > "$inventory"
    printf 'candidate=bad\n' > "$case_root/templates/bad.conf"
    printf 'candidate=good\n' > "$case_root/templates/good.conf"
    mkdir -p "$case_root/managed/bad1/etc" "$case_root/managed/good1/etc"
    printf 'original=bad1\n' > "$case_root/managed/bad1/etc/app.conf"
    printf 'original=good1\n' > "$case_root/managed/good1/etc/app.conf"
    cat > "$case_root/validators/validate" <<'EOF'
#!/usr/bin/env bash
[[ $1 != bad1 ]] && grep -Fq 'candidate=good' "$2"
EOF
    chmod +x "$case_root/validators/validate"

    status=0
    run_apply --group app > "$case_root/output" || status=$?

    [[ $status -eq 1 ]] &&
        assert_file_content original=bad1 "$case_root/managed/bad1/etc/app.conf" &&
        assert_file_content candidate=good "$case_root/managed/good1/etc/app.conf" &&
        assert_file_content original=bad1 "$case_root/backups/bad1/etc/app.conf" &&
        assert_contains $'ROLLED_BACK\tbad1\tetc/app.conf\t' "$journal" &&
        assert_contains $'COMMITTED\tgood1\tetc/app.conf\t' "$journal"
}

test_new_file_is_removed_on_validation_failure() {
    new_case absent
    printf 'new1\tapp\tetc/new.conf\tnew.conf\treject\n' > "$inventory"
    printf 'invalid=true\n' > "$case_root/templates/new.conf"
    cat > "$case_root/validators/reject" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$case_root/validators/reject"

    status=0
    run_apply > "$case_root/output" || status=$?

    [[ $status -eq 1 ]] &&
        assert_no_path "$case_root/managed/new1/etc/new.conf" &&
        [[ -f $case_root/backups/new1/etc/new.conf.configapply-absent ]] &&
        assert_contains $'ROLLED_BACK\tnew1\tetc/new.conf\t' "$journal"
}

test_identical_apply_is_idempotent() {
    new_case idempotent
    printf 'cache1\tcache\tetc/cache.conf\tcache.conf\t-\n' > "$inventory"
    printf 'size=64\n' > "$case_root/templates/cache.conf"
    mkdir -p "$case_root/managed/cache1/etc"
    printf 'size=64\n' > "$case_root/managed/cache1/etc/cache.conf"

    run_apply > "$case_root/output"

    assert_file_content size=64 "$case_root/managed/cache1/etc/cache.conf" &&
        [[ ! -s $journal ]] &&
        assert_no_path "$case_root/backups/cache1" &&
        assert_contains $'UNCHANGED\tcache1\tetc/cache.conf' "$case_root/output"
}

test_invalid_inventory_is_rejected_before_escape() {
    new_case invalid
    printf 'host1\tapp\t../outside\tapp.conf\t-\n' > "$inventory"
    printf 'payload\n' > "$case_root/templates/app.conf"

    status=0
    run_apply > "$case_root/output" 2> "$case_root/error" || status=$?

    [[ $status -eq 2 ]] && assert_no_path "$case_root/outside"
}

run_test 'exact host selection preserves untargeted similarly named host' test_exact_host_selection_preserves_untargeted_host
run_test 'group selection matches complete group names' test_group_selection_is_exact
run_test 'check mode validates without mutation' test_check_mode_is_non_mutating
run_test 'changed file has backup, journal, and atomic cleanup' test_change_has_backup_and_journal
run_test 'validation rollback is isolated and processing continues' test_validation_failure_rolls_back_and_continues
run_test 'failed new file is removed during rollback' test_new_file_is_removed_on_validation_failure
run_test 'identical application is idempotent' test_identical_apply_is_idempotent
run_test 'unsafe inventory destination is rejected' test_invalid_inventory_is_rejected_before_escape

printf '%d passed; %d failed\n' "$passed" "$failed"
((failed == 0))
