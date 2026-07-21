#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly TEST_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly PROJECT_DIR=$(cd "$TEST_DIR/.." && pwd)
readonly TOOL="$PROJECT_DIR/bin/configdrift"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_contains() {
    local file=$1 text=$2
    grep -Fq "$text" "$file" || fail "$file does not contain: $text"
}

assert_not_contains() {
    local file=$1 text=$2
    if grep -Fq "$text" "$file"; then
        fail "$file unexpectedly contains: $text"
    fi
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
root="$work/root"
mkdir -p "$root/etc/acme" "$root/etc/ssh"

printf '%s\n' 'site-approved' > "$work/approved-banner"
printf '%s\n' 'site-local' > "$root/etc/acme/banner.conf"
printf '%s\n' 'site-local' > "$root/etc/acme/banner.conf.bak"
printf '%s\n' 'PermitRootLogin no' > "$root/etc/ssh/sshd_config"
chmod 0644 "$root/etc/acme/banner.conf" "$root/etc/acme/banner.conf.bak"
chmod 0644 "$root/etc/ssh/sshd_config"

approved_banner=$(base64 < "$work/approved-banner" | tr -d '\n')
local_banner=$(base64 < "$root/etc/acme/banner.conf" | tr -d '\n')
sshd_content=$(base64 < "$root/etc/ssh/sshd_config" | tr -d '\n')
uid=$(id -u)
gid=$(id -g)

printf 'FILE\t/etc/acme/banner.conf\t0644\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$approved_banner" > "$work/baseline.tsv"
printf 'FILE\t/etc/acme/banner.conf.bak\t0644\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$approved_banner" >> "$work/baseline.tsv"
printf 'FILE\t/etc/ssh/sshd_config\t0600\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$sshd_content" >> "$work/baseline.tsv"
printf 'SERVICE\tsshd\tenabled\tactive\n' >> "$work/baseline.tsv"
printf 'SERVICE\tacme-agent\tenabled\tactive\n' >> "$work/baseline.tsv"
printf 'SERVICE\tacme-agent-backup\tenabled\tactive\n' >> "$work/baseline.tsv"

printf 'FILE\t/etc/acme/banner.conf\t0644\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$local_banner" > "$work/host.tsv"
printf 'FILE\t/etc/acme/banner.conf.bak\t0644\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$local_banner" >> "$work/host.tsv"
printf 'FILE\t/etc/ssh/sshd_config\t0644\t%s\t%s\t%s\n' \
    "$uid" "$gid" "$sshd_content" >> "$work/host.tsv"
printf 'SERVICE\tsshd\tdisabled\tinactive\n' >> "$work/host.tsv"
printf 'SERVICE\tacme-agent\tdisabled\tinactive\n' >> "$work/host.tsv"
printf 'SERVICE\tacme-agent-backup\tdisabled\tinactive\n' >> "$work/host.tsv"

printf 'FILE\t/etc/acme/banner.conf\tcontent\t%s\n' "$local_banner" > "$work/exceptions.tsv"
printf 'FILE\t/etc/ssh/sshd_config\tcontent\t%s\n' "$sshd_content" >> "$work/exceptions.tsv"
printf 'FILE\t/etc/ssh/sshd_config\tmode\t0666\n' >> "$work/exceptions.tsv"
printf 'SERVICE\tacme-agent\tenabled\tdisabled\n' >> "$work/exceptions.tsv"
printf 'SERVICE\tacme-agent\tactive\tinactive\n' >> "$work/exceptions.tsv"

printf 'sshd\tdisabled\tinactive\n' > "$work/services.tsv"
printf 'acme-agent\tdisabled\tinactive\n' >> "$work/services.tsv"
printf 'acme-agent-backup\tdisabled\tinactive\n' >> "$work/services.tsv"

bash "$TOOL" analyze \
    "$work/baseline.tsv" "$work/host.tsv" "$work/exceptions.tsv" \
    "$work/report.tsv" "$work/repair.sh"
bash "$TOOL" analyze \
    "$work/baseline.tsv" "$work/host.tsv" "$work/exceptions.tsv" \
    "$work/report-again.tsv" "$work/repair-again.sh"
cmp -s "$work/report.tsv" "$work/report-again.tsv" || fail 'report is not deterministic'
cmp -s "$work/repair.sh" "$work/repair-again.sh" || fail 'repair is not deterministic'

assert_contains "$work/report.tsv" $'INTENTIONAL\tFILE\t/etc/acme/banner.conf\tcontent'
assert_contains "$work/report.tsv" $'DRIFT\tFILE\t/etc/acme/banner.conf.bak\tcontent'
assert_contains "$work/report.tsv" $'DRIFT\tFILE\t/etc/ssh/sshd_config\tmode'
assert_contains "$work/report.tsv" $'DRIFT\tSERVICE\tsshd\tenabled'
assert_contains "$work/report.tsv" $'DRIFT\tSERVICE\tsshd\tactive'
assert_contains "$work/report.tsv" $'INTENTIONAL\tSERVICE\tacme-agent\tenabled'
assert_contains "$work/report.tsv" $'INTENTIONAL\tSERVICE\tacme-agent\tactive'
assert_contains "$work/report.tsv" $'DRIFT\tSERVICE\tacme-agent-backup\tenabled'
assert_contains "$work/report.tsv" $'DRIFT\tSERVICE\tacme-agent-backup\tactive'

assert_not_contains "$work/repair.sh" 'repair_content /etc/acme/banner.conf '
assert_contains "$work/repair.sh" 'repair_content /etc/acme/banner.conf.bak '
assert_not_contains "$work/repair.sh" 'repair_service_field acme-agent '
assert_contains "$work/repair.sh" 'repair_service_field acme-agent-backup enabled enabled'
assert_contains "$work/repair.sh" 'repair_service_field acme-agent-backup active active'
repair_calls=$(grep -Ec '^repair_(content|mode|owner_fields|service_field) ' "$work/repair.sh")
[[ "$repair_calls" == 6 ]] || fail "repair is not minimal: found $repair_calls calls instead of 6"

# Ownership repair must preserve a separately excepted ownership field. This
# scenario only inspects the generated repair because its synthetic IDs should
# not be applied on the test host.
printf 'FILE\t/etc/acme/owned.conf\t0644\t41001\t42001\t%s\n' \
    "$local_banner" > "$work/owner-baseline.tsv"
printf 'FILE\t/etc/acme/owned.conf\t0644\t41002\t42002\t%s\n' \
    "$local_banner" > "$work/owner-host.tsv"
printf 'FILE\t/etc/acme/owned.conf\tgid\t42002\n' > "$work/owner-exceptions.tsv"
bash "$TOOL" analyze \
    "$work/owner-baseline.tsv" "$work/owner-host.tsv" "$work/owner-exceptions.tsv" \
    "$work/owner-report.tsv" "$work/owner-repair.sh"
assert_contains "$work/owner-report.tsv" $'DRIFT\tFILE\t/etc/acme/owned.conf\tuid'
assert_contains "$work/owner-report.tsv" $'INTENTIONAL\tFILE\t/etc/acme/owned.conf\tgid'
assert_contains "$work/owner-repair.sh" 'repair_owner_fields /etc/acme/owned.conf 41001 -'
[[ $(grep -Ec '^repair_owner_fields ' "$work/owner-repair.sh") == 1 ]] ||
    fail 'ownership repair is not minimal or field-scoped'

bash "$work/repair.sh" "$root" "$work/services.tsv"
first_hash=$(sha256sum "$work/services.tsv" "$root/etc/acme/banner.conf.bak" "$root/etc/ssh/sshd_config")
bash "$work/repair.sh" "$root" "$work/services.tsv"
second_hash=$(sha256sum "$work/services.tsv" "$root/etc/acme/banner.conf.bak" "$root/etc/ssh/sshd_config")
[[ "$first_hash" == "$second_hash" ]] || fail 'repair is not idempotent'

[[ $(< "$root/etc/acme/banner.conf") == site-local ]] || fail 'intentional exception was repaired'
[[ $(< "$root/etc/acme/banner.conf.bak") == site-approved ]] || fail 'neighboring drift was not repaired'
[[ $(stat -c '%a' "$root/etc/acme/banner.conf.bak") == 644 ]] || fail 'content repair changed file mode'
[[ $(stat -c '%u:%g' "$root/etc/acme/banner.conf.bak") == "$uid:$gid" ]] || fail 'content repair changed file ownership'
[[ $(stat -c '%a' "$root/etc/ssh/sshd_config") == 600 ]] || fail 'file mode was not repaired'
[[ $(< "$work/services.tsv") == $'sshd\tenabled\tactive\nacme-agent\tdisabled\tinactive\nacme-agent-backup\tenabled\tactive' ]] ||
    fail 'service behavior was not repaired'

verify_output=$(bash "$TOOL" verify \
    "$work/baseline.tsv" "$root" "$work/services.tsv" "$work/exceptions.tsv") ||
    fail 'verification still reports drift'
[[ "$verify_output" == $'INTENTIONAL\tFILE\t/etc/acme/banner.conf\tcontent\nINTENTIONAL\tSERVICE\tacme-agent\tenabled\nINTENTIONAL\tSERVICE\tacme-agent\tactive' ]] ||
    fail 'verification did not isolate the intentional exception'

printf 'PASS: exact exceptions, minimal repair, idempotence, permissions, and service state\n'
