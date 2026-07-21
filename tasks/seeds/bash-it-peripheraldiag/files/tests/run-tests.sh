#!/usr/bin/env bash

set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tool=$root/peripheraldiag
fixture_root=$root/fixtures
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

checks=0
failures=0
output=
status=0

record() {
    local result=$1
    local name=$2
    checks=$((checks + 1))
    if [[ $result == pass ]]; then
        echo "ok $checks - $name"
    else
        echo "not ok $checks - $name"
        failures=$((failures + 1))
    fi
}

run_tool() {
    output=$(bash "$tool" "$@" 2>&1)
    status=$?
}

contains() {
    [[ $output == *"$1"* ]]
}

copy_fixture() {
    local source=$1
    local name=$2
    local destination=$scratch/$name
    mkdir -p "$destination"
    cp "$fixture_root/$source/devices.tsv" "$destination/devices.tsv"
    echo "$destination"
}

row_for() {
    local table=$1
    local wanted=$2
    awk -F '\t' -v wanted="$wanted" '!/^#/ && $6 == wanted { print; exit }' "$table"
}

field_for() {
    local table=$1
    local wanted=$2
    local field=$3
    awk -F '\t' -v wanted="$wanted" -v field="$field" \
        '!/^#/ && $6 == wanted { print $field; exit }' "$table"
}

set_field() {
    local table=$1
    local wanted=$2
    local field=$3
    local value=$4
    local edited=${table}.edited
    awk -F '\t' -v OFS='\t' -v wanted="$wanted" -v field="$field" -v value="$value" '
        /^#/ { print; next }
        {
            if ($6 == wanted) {
                $field = value
            }
            print
        }
    ' "$table" >"$edited"
    mv -- "$edited" "$table"
}

run_tool --fixture "$fixture_root/connected" --serial SCANNER-001
if [[ $status -eq 1 ]] \
    && contains 'enumeration: ok (port=1-2 bus=001 device=004 id=1209:0001 serial=SCANNER-001)' \
    && contains 'power: ok (authorized=1 draw=100mA)' \
    && contains 'driver: ok (usbhid)' \
    && contains 'permissions: blocked (node=/dev/hidraw2 mode=0600 group=plugdev; need mode=0660 group=plugdev)' \
    && contains 'application-claim: blocked (permissions)' \
    && contains 'diagnosis: permissions'; then
    record pass 'reports all layers and isolates the permission fault'
else
    record fail 'reports all layers and isolates the permission fault'
fi

power_fixture=$(copy_fixture connected power)
set_field "$power_fixture/devices.tsv" SCANNER-001 7 0
run_tool --fixture "$power_fixture" --serial SCANNER-001
if [[ $status -eq 1 ]] && contains 'power: blocked (authorized=0 draw=100mA limit=500mA)' \
    && contains 'diagnosis: power'; then
    record pass 'identifies a power authorization fault'
else
    record fail 'identifies a power authorization fault'
fi

driver_fixture=$(copy_fixture connected driver)
set_field "$driver_fixture/devices.tsv" SCANNER-001 9 -
run_tool --fixture "$driver_fixture" --serial SCANNER-001
if [[ $status -eq 1 ]] && contains 'driver: blocked (unbound)' \
    && contains 'diagnosis: driver'; then
    record pass 'identifies an unbound driver'
else
    record fail 'identifies an unbound driver'
fi

claim_fixture=$(copy_fixture connected claim)
set_field "$claim_fixture/devices.tsv" SCANNER-001 11 0660
set_field "$claim_fixture/devices.tsv" SCANNER-001 13 claimed:camera-ui
run_tool --fixture "$claim_fixture" --serial SCANNER-001
if [[ $status -eq 1 ]] && contains 'permissions: ok' \
    && contains 'application-claim: busy (claimed:camera-ui)' \
    && contains 'diagnosis: application-claim'; then
    record pass 'identifies an application claim conflict'
else
    record fail 'identifies an application claim conflict'
fi

run_tool --fixture "$fixture_root/connected" --serial ABSENT-999
if [[ $status -eq 2 ]] && contains 'enumeration: missing (serial=ABSENT-999)' \
    && contains 'diagnosis: enumeration'; then
    record pass 'identifies a missing enumeration'
else
    record fail 'identifies a missing enumeration'
fi

repair_fixture=$(copy_fixture connected repair)
peer_before=$(row_for "$repair_fixture/devices.tsv" CALIBRATOR-002)
keyboard_before=$(row_for "$repair_fixture/devices.tsv" KEYBOARD-003)
run_tool --fixture "$repair_fixture" --serial SCANNER-001 --repair
peer_after=$(row_for "$repair_fixture/devices.tsv" CALIBRATOR-002)
keyboard_after=$(row_for "$repair_fixture/devices.tsv" KEYBOARD-003)
target_mode=$(field_for "$repair_fixture/devices.tsv" SCANNER-001 11)
if [[ $status -eq 0 && $target_mode == 0660 && $peer_after == "$peer_before" \
    && $keyboard_after == "$keyboard_before" ]] \
    && contains 'diagnosis: healthy'; then
    record pass 'repairs only the selected physical device'
else
    record fail 'repairs only the selected physical device'
fi

reconnect_fixture=$(copy_fixture reconnected reconnect)
peer_before=$(row_for "$reconnect_fixture/devices.tsv" CALIBRATOR-002)
keyboard_before=$(row_for "$reconnect_fixture/devices.tsv" KEYBOARD-003)
run_tool --fixture "$reconnect_fixture" --serial SCANNER-001 --repair
peer_after=$(row_for "$reconnect_fixture/devices.tsv" CALIBRATOR-002)
keyboard_after=$(row_for "$reconnect_fixture/devices.tsv" KEYBOARD-003)
target_mode=$(field_for "$reconnect_fixture/devices.tsv" SCANNER-001 11)
if [[ $status -eq 0 && $target_mode == 0660 && $peer_after == "$peer_before" \
    && $keyboard_after == "$keyboard_before" ]] \
    && contains 'enumeration: ok (port=3-2 bus=003 device=011 id=1209:0001 serial=SCANNER-001)' \
    && contains 'permissions: ok (node=/dev/hidraw7 mode=0660 group=plugdev)' \
    && contains 'application-claim: ok (available)' \
    && contains 'diagnosis: healthy'; then
    record pass 'uses the stable serial after reconnect without touching its peer'
else
    record fail 'uses the stable serial after reconnect without touching its peer'
fi

if ((failures)); then
    echo "$failures of $checks tests failed" >&2
    exit 1
fi

echo "all $checks tests passed"
