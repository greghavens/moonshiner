#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")/.."

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_eq() {
    local expected=$1 actual=$2 context=$3
    [[ $actual == "$expected" ]] ||
        fail "$context (expected <$expected>, got <$actual>)"
}

assert_file_mode() {
    local expected=$1 path=$2
    local actual
    actual=$(stat -c '%a' -- "$path")
    assert_eq "$expected" "$actual" "mode for $path"
}

assert_owner() {
    local expected=$1 path=$2
    local actual
    actual=$(stat -c '%u:%g' -- "$path")
    assert_eq "$expected" "$actual" "owner for $path"
}

make_manifest() {
    local output=$1
    sed -e "s/@UID@/$(id -u)/g" -e "s/@GID@/$(id -g)/g" \
        fixtures/manifest.tsv.in > "$output"
}

populate_case() {
    local root=$1
    mkdir -p "$root/etc/file-service" "$root/lib/file-service/hooks" \
        "$root/var/lib/file-service/uploads"
    printf '%s\n' \
        '# locally altered listener' \
        'listen_address=0.0.0.0' \
        'listen_port=8080' \
        'upload_root=/tmp/public' > "$root/etc/file-service/service.conf"
    printf '%s\n' \
        '# injected route' \
        'incoming/*	exfiltration/' > "$root/etc/file-service/routing.rules"
    cp fixtures/approved/lib/file-service/hooks/post-upload \
        "$root/lib/file-service/hooks/post-upload"
    cp fixtures/approved/lib/file-service/stable.conf \
        "$root/lib/file-service/stable.conf"
    chmod 640 "$root/etc/file-service/service.conf"
    chmod 600 "$root/etc/file-service/routing.rules"
    chmod 700 "$root/lib/file-service/hooks/post-upload"
    chmod 644 "$root/lib/file-service/stable.conf"
    printf '%s\n' 'customer payload: do not alter' \
        > "$root/var/lib/file-service/uploads/customer.bin"
    chmod 600 "$root/var/lib/file-service/uploads/customer.bin"
}

run_case() {
    local base=$1
    mkdir -p "$base"
    populate_case "$base/root"
    make_manifest "$base/manifest.tsv"
    bash bin/file-integrity \
        --manifest "$base/manifest.tsv" \
        --root "$base/root" \
        --fixtures fixtures/approved \
        --quarantine "$base/quarantine" \
        --evidence "$base/evidence" \
        > "$base/stdout" 2> "$base/stderr"
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/file-integrity-test.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT HUP INT TERM

# The main incident contains two content mismatches, one missing file,
# permission-only drift, one exact match, and one unlisted file.
run_case "$tmp/case1"
assert_eq 'file-integrity: verified=1 repaired=4 quarantined=1' \
    "$(cat "$tmp/case1/stdout")" "summary"
[[ ! -s $tmp/case1/stderr ]] || fail "successful run wrote stderr"

for relative in \
    etc/file-service/service.conf \
    etc/file-service/routing.rules \
    etc/file-service/banner.txt \
    lib/file-service/hooks/post-upload \
    lib/file-service/stable.conf; do
    cmp "fixtures/approved/$relative" "$tmp/case1/root/$relative" ||
        fail "managed content was not restored: $relative"
    assert_owner "$(id -u):$(id -g)" "$tmp/case1/root/$relative"
done
assert_file_mode 640 "$tmp/case1/root/etc/file-service/service.conf"
assert_file_mode 640 "$tmp/case1/root/etc/file-service/routing.rules"
assert_file_mode 644 "$tmp/case1/root/etc/file-service/banner.txt"
assert_file_mode 750 "$tmp/case1/root/lib/file-service/hooks/post-upload"
assert_file_mode 644 "$tmp/case1/root/lib/file-service/stable.conf"
assert_eq 'customer payload: do not alter' \
    "$(cat "$tmp/case1/root/var/lib/file-service/uploads/customer.bin")" \
    "unlisted file content"
assert_file_mode 600 "$tmp/case1/root/var/lib/file-service/uploads/customer.bin"

# Only the corrupt listener is both present and explicitly authorized for
# quarantine. The permission-only hook is authorized but is not corrupt.
mapfile -t quarantine_files < <(
    find "$tmp/case1/quarantine" -type f -printf '%P\n' | LC_ALL=C sort
)
assert_eq 1 "${#quarantine_files[@]}" "quarantine file count"
assert_eq 'etc/file-service/service.conf.corrupt' \
    "${quarantine_files[0]}" "quarantine path"
expected_bad_service=$(printf '%s\n' \
    '# locally altered listener' \
    'listen_address=0.0.0.0' \
    'listen_port=8080' \
    'upload_root=/tmp/public')
assert_eq "$expected_bad_service" \
    "$(cat "$tmp/case1/quarantine/etc/file-service/service.conf.corrupt")" \
    "quarantined bytes"

uid=$(id -u)
gid=$(id -g)
owner=$uid:$gid
service_good=$(sha256sum fixtures/approved/etc/file-service/service.conf)
service_good=${service_good%% *}
routing_good=$(sha256sum fixtures/approved/etc/file-service/routing.rules)
routing_good=${routing_good%% *}
banner_good=$(sha256sum fixtures/approved/etc/file-service/banner.txt)
banner_good=${banner_good%% *}
hook_good=$(sha256sum fixtures/approved/lib/file-service/hooks/post-upload)
hook_good=${hook_good%% *}
stable_good=$(sha256sum fixtures/approved/lib/file-service/stable.conf)
stable_good=${stable_good%% *}
service_bad=$(sha256sum "$tmp/case1/quarantine/etc/file-service/service.conf.corrupt")
service_bad=${service_bad%% *}
printf '%s\n' \
    '# injected route' \
    'incoming/*	exfiltration/' > "$tmp/bad-routing"
routing_bad=$(sha256sum "$tmp/bad-routing")
routing_bad=${routing_bad%% *}

expected_audit=$tmp/expected-audit.tsv
{
    printf 'path\tinitial_state\texpected_sha256\tbefore_sha256\tafter_sha256\texpected_owner\tbefore_owner\tafter_owner\texpected_mode\tbefore_mode\tafter_mode\taction\n'
    printf 'etc/file-service/service.conf\tcontent-mismatch\t%s\t%s\t%s\t%s\t%s\t%s\t640\t640\t640\tquarantine-restore\n' \
        "$service_good" "$service_bad" "$service_good" "$owner" "$owner" "$owner"
    printf 'etc/file-service/routing.rules\tcontent-mismatch\t%s\t%s\t%s\t%s\t%s\t%s\t640\t600\t640\trestore\n' \
        "$routing_good" "$routing_bad" "$routing_good" "$owner" "$owner" "$owner"
    printf 'etc/file-service/banner.txt\tmissing\t%s\t-\t%s\t%s\t-\t%s\t644\t-\t644\tcreate\n' \
        "$banner_good" "$banner_good" "$owner" "$owner"
    printf 'lib/file-service/hooks/post-upload\tmetadata-mismatch\t%s\t%s\t%s\t%s\t%s\t%s\t750\t700\t750\tmetadata-repair\n' \
        "$hook_good" "$hook_good" "$hook_good" "$owner" "$owner" "$owner"
    printf 'lib/file-service/stable.conf\tok\t%s\t%s\t%s\t%s\t%s\t%s\t644\t644\t644\tverified\n' \
        "$stable_good" "$stable_good" "$stable_good" "$owner" "$owner" "$owner"
} > "$expected_audit"
cmp "$expected_audit" "$tmp/case1/evidence/audit.tsv" ||
    fail "audit.tsv does not exactly describe before and after state"

# Verify the custody schema, event order, semantic fields, and every hash link.
expected_actions=(
    quarantine-restore
    restore
    create
    metadata-repair
)
expected_paths=(
    etc/file-service/service.conf
    etc/file-service/routing.rules
    etc/file-service/banner.txt
    lib/file-service/hooks/post-upload
)
expected_before=("$service_bad" "$routing_bad" - "$hook_good")
expected_after=("$service_good" "$routing_good" "$banner_good" "$hook_good")
expected_quarantine=(
    etc/file-service/service.conf.corrupt
    -
    -
    -
)
{
    IFS= read -r header
    assert_eq $'sequence\taction\tpath\tbefore_sha256\tafter_sha256\tquarantine_path\tprevious_link\tlink' \
        "$header" "custody header"
    row=0
    previous=GENESIS
    while IFS=$'\t' read -r sequence action path before after quarantine previous_link link extra; do
        [[ -z ${extra:-} ]] || fail "custody row has extra fields"
        assert_eq "$((row + 1))" "$sequence" "custody sequence"
        assert_eq "${expected_actions[row]}" "$action" "custody action"
        assert_eq "${expected_paths[row]}" "$path" "custody path"
        assert_eq "${expected_before[row]}" "$before" "custody before hash"
        assert_eq "${expected_after[row]}" "$after" "custody after hash"
        assert_eq "${expected_quarantine[row]}" "$quarantine" "custody quarantine path"
        assert_eq "$previous" "$previous_link" "custody previous link"
        record=$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s' \
            "$sequence" "$action" "$path" "$before" "$after" "$quarantine" "$previous_link")
        calculated=$(printf '%s' "$record" | sha256sum)
        calculated=${calculated%% *}
        assert_eq "$calculated" "$link" "custody link"
        previous=$link
        row=$((row + 1))
    done
    assert_eq 4 "$row" "custody event count"
} < "$tmp/case1/evidence/custody.tsv"

# A second identical captured case must produce byte-identical evidence and
# quarantine output: no timestamps, random IDs, or absolute temp paths leak in.
run_case "$tmp/case2"
cmp "$tmp/case1/stdout" "$tmp/case2/stdout" ||
    fail "summary is not deterministic"
cmp "$tmp/case1/evidence/audit.tsv" "$tmp/case2/evidence/audit.tsv" ||
    fail "audit is not deterministic"
cmp "$tmp/case1/evidence/custody.tsv" "$tmp/case2/evidence/custody.tsv" ||
    fail "custody ledger is not deterministic"
cmp "$tmp/case1/quarantine/etc/file-service/service.conf.corrupt" \
    "$tmp/case2/quarantine/etc/file-service/service.conf.corrupt" ||
    fail "quarantine evidence is not deterministic"

# Invalid local restore evidence must fail closed before the captured root or
# either output destination is touched.
mkdir -p "$tmp/preflight"
populate_case "$tmp/preflight/root"
make_manifest "$tmp/preflight/manifest.tsv"
cp -R fixtures/approved "$tmp/preflight/approved"
printf '%s\n' 'tampered local fixture' \
    > "$tmp/preflight/approved/etc/file-service/routing.rules"
preflight_service=$(sha256sum "$tmp/preflight/root/etc/file-service/service.conf")
preflight_service=${preflight_service%% *}
if bash bin/file-integrity \
    --manifest "$tmp/preflight/manifest.tsv" \
    --root "$tmp/preflight/root" \
    --fixtures "$tmp/preflight/approved" \
    --quarantine "$tmp/preflight/quarantine" \
    --evidence "$tmp/preflight/evidence" \
    > "$tmp/preflight/stdout" 2> "$tmp/preflight/stderr"; then
    fail "tampered restore fixture was accepted"
fi
assert_eq "$preflight_service" \
    "$(sha256sum "$tmp/preflight/root/etc/file-service/service.conf" | cut -d' ' -f1)" \
    "preflight failure changed managed content"
[[ ! -e $tmp/preflight/quarantine ]] ||
    fail "preflight failure created quarantine output"
[[ ! -e $tmp/preflight/evidence ]] ||
    fail "preflight failure created evidence output"
if find "$tmp/preflight/root" -name '.file-integrity.stage.*' -print -quit |
   grep -q .; then
    fail "preflight failure left a staging artifact"
fi

# A managed path that crosses a symlink must not reach or alter the target.
mkdir -p "$tmp/symlink/root" "$tmp/symlink/outside/file-service"
make_manifest "$tmp/symlink/manifest.tsv"
printf '%s\n' 'outside sentinel' \
    > "$tmp/symlink/outside/file-service/service.conf"
ln -s "$tmp/symlink/outside" "$tmp/symlink/root/etc"
outside_before=$(sha256sum "$tmp/symlink/outside/file-service/service.conf")
outside_before=${outside_before%% *}
if bash bin/file-integrity \
    --manifest "$tmp/symlink/manifest.tsv" \
    --root "$tmp/symlink/root" \
    --fixtures fixtures/approved \
    --quarantine "$tmp/symlink/quarantine" \
    --evidence "$tmp/symlink/evidence" \
    > "$tmp/symlink/stdout" 2> "$tmp/symlink/stderr"; then
    fail "managed symlink traversal was accepted"
fi
assert_eq "$outside_before" \
    "$(sha256sum "$tmp/symlink/outside/file-service/service.conf" | cut -d' ' -f1)" \
    "symlink rejection changed outside content"
[[ ! -e $tmp/symlink/quarantine && ! -e $tmp/symlink/evidence ]] ||
    fail "symlink rejection created output"

printf '%s\n' 'PASS: file integrity remediation and custody evidence'
