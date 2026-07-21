#!/usr/bin/env bash
set -euo pipefail

root=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)
doctor="$root/nfsexport-doctor.sh"
incident="$root/incident"
test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT
pass_count=0

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

pass() {
    pass_count=$((pass_count + 1))
    printf 'ok %d - %s\n' "$pass_count" "$1"
}

diagnosis=$test_tmp/diagnosis.txt
if bash "$doctor" diagnose "$incident" > "$diagnosis"; then
    fail 'the captured incident must report denied access'
fi
grep -Fq 'client=builder-a address=10.44.18.31 network=ok identity=ok mount=ok export=ok filesystem=ok access=ok' "$diagnosis" ||
    fail 'builder-a should be healthy across every layer'
grep -Fq 'client=builder-b address=10.44.18.32 network=ok identity=ok mount=ok export=missing filesystem=ok access=denied' "$diagnosis" ||
    fail 'builder-b should be isolated to the export layer'
pass 'diagnosis checks all five layers and isolates the export fault'

fixed_exports=$test_tmp/exports
cp "$incident/exports" "$fixed_exports"
fix_output=$test_tmp/fix.txt
bash "$doctor" fix "$incident" "$fixed_exports" > "$fix_output" ||
    fail 'fix should repair and verify the export-only incident'
expected='/srv/releases 10.44.18.31(rw,sync,root_squash,no_subtree_check) 10.44.18.32(rw,sync,root_squash,no_subtree_check)'
[[ $(<"$fixed_exports") == "$expected" ]] ||
    fail 'fix must preserve the rule and add only builder-b with the established options'
[[ $(grep -o '10\.44\.18\.31' "$fixed_exports" | wc -l) -eq 1 ]] ||
    fail 'the working client must remain exactly once'
[[ $(grep -o '10\.44\.18\.32' "$fixed_exports" | wc -l) -eq 1 ]] ||
    fail 'the missing client must be explicit and appear exactly once'
if grep -Eq '(^|[[:space:]])([^[:space:]()]*\*|0\.0\.0\.0|[^[:space:]()]*/[0-9]+)\(' "$fixed_exports"; then
    fail 'wildcard, world, and subnet exports are forbidden'
fi
grep -Fq 'verify: builder-a ok' "$fix_output" || fail 'fix must verify builder-a'
grep -Fq 'verify: builder-b ok' "$fix_output" || fail 'fix must verify builder-b'
pass 'least local change grants two explicit clients and verifies both'

bash "$doctor" verify "$incident" "$fixed_exports" > "$test_tmp/verify.txt" ||
    fail 'standalone verification should pass after repair'
[[ $(grep -c '^verify: .* ok$' "$test_tmp/verify.txt") -eq 2 ]] ||
    fail 'standalone verification must cover exactly both captured clients'
pass 'standalone verification covers both clients'

for fault in network identity mount filesystem; do
    bad_snapshot=$test_tmp/$fault-fault
    mkdir "$bad_snapshot"
    cp "$incident/server.conf" "$bad_snapshot/server.conf"
    cp "$incident/clients.tsv" "$bad_snapshot/clients.tsv"
    cp "$incident/exports" "$bad_snapshot/exports"
    case $fault in
        network)
            sed $'s/builder-b\t10.44.18.32\tok/builder-b\t10.44.18.32\tblocked/' \
                "$incident/clients.tsv" > "$bad_snapshot/clients.tsv"
            ;;
        identity)
            sed $'s/\t2400\t2400\trw,vers=4.2,hard$/\t2500\t2500\trw,vers=4.2,hard/' \
                "$incident/clients.tsv" > "$bad_snapshot/clients.tsv"
            ;;
        mount)
            sed $'s/\trw,vers=4.2,hard$/\tro,vers=4.2,hard/' \
                "$incident/clients.tsv" > "$bad_snapshot/clients.tsv"
            ;;
        filesystem)
            sed 's/^mode=.*/mode=0550/' "$incident/server.conf" > "$bad_snapshot/server.conf"
            ;;
    esac
    before=$(<"$bad_snapshot/exports")
    if bash "$doctor" fix "$bad_snapshot" > "$test_tmp/$fault.out" 2> "$test_tmp/$fault.err"; then
        fail "a $fault fault must not be treated as an export repair"
    fi
    [[ $(<"$bad_snapshot/exports") == "$before" ]] ||
        fail "$fault faults must leave exports byte-for-byte unchanged"
    grep -Fq 'no export-only fault found; unchanged' "$test_tmp/$fault.err" ||
        fail "$fault faults should explain that no local export fix applies"
done
pass 'network, identity, mount, and filesystem faults are not papered over'

printf '1..%d\n' "$pass_count"
