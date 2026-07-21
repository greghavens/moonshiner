#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_report_line() {
    local report=$1
    local expected=$2
    grep -Fqx "$expected" "$report" || fail "missing report line: $expected"
}

make_common_fstab() {
    local directory=$1
    cat >"$directory/fstab" <<'EOF'
# protected test table
UUID=11111111-2222-3333-4444-555555555555 / ext4 defaults 0 1
UUID=7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9 /srv/archive ext4 defaults,nofail,uid=1000,gid=1000,x-systemd.device-timeout=10s 0 2
EOF
}

late_case="$scratch/late-device"
late_out="$scratch/late-output"
mkdir -p "$late_case" "$late_out"
make_common_fstab "$late_case"
cat >"$late_case/device-identity.txt" <<'EOF'
/dev/nvme1n1p1: LABEL="archive" UUID="7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9" BLOCK_SIZE="4096" TYPE="ext4"
EOF
cat >"$late_case/filesystem-state.txt" <<'EOF'
e2fsck 1.47.0 (5-Feb-2023)
/dev/nvme1n1p1: clean, 48192/61054976 files, 9031181/244190208 blocks
EOF
cat >"$late_case/boot.log" <<'EOF'
Jul 18 03:14:06 host systemd[1]: Timed out waiting for device /dev/disk/by-uuid/7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9.
Jul 18 03:17:42 host kernel: nvme nvme1: device is now ready
Jul 18 03:18:09 host kernel: EXT4-fs (nvme1n1p1): Unrecognized mount option "uid=1000" or missing value
EOF

marker="$scratch/fake-mount.marker"
PATH="$project_dir/tests/mockbin:$PATH" \
MOCK_MOUNT_MARKER="$marker" \
    "$project_dir/mountrecover" "$late_case" /srv/archive "$late_out"

late_report="$late_out/report.env"
assert_report_line "$late_report" 'STATUS=BAD_OPTIONS'
assert_report_line "$late_report" 'REASON=the device is present and clean, but ext-family ownership options were rejected'
assert_report_line "$late_report" 'DEVICE_PRESENT=yes'
assert_report_line "$late_report" 'DEVICE=/dev/nvme1n1p1'
assert_report_line "$late_report" 'EXPECTED_FILESYSTEM=ext4'
assert_report_line "$late_report" 'ACTUAL_FILESYSTEM=ext4'
assert_report_line "$late_report" 'FILESYSTEM_STATE=clean'
assert_report_line "$late_report" 'FILESYSTEM_SUMMARY=/dev/nvme1n1p1: clean, 48192/61054976 files, 9031181/244190208 blocks'
assert_report_line "$late_report" 'EARLY_DEVICE_TIMEOUT=yes'
assert_report_line "$late_report" 'OPTION_REJECTION=yes'
assert_report_line "$late_report" 'REMOVED_OPTIONS=uid=1000,gid=1000'
assert_report_line "$late_report" 'CORRECTED_ENTRY=UUID=7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9 /srv/archive ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2'
assert_report_line "$late_report" 'VALIDATION=passed: mount --fake --no-mtab --all --fstab corrected.fstab'
assert_report_line "$late_report" 'RECOVERY_REVERSAL=mount -o remount,rw / ; cp -a /etc/fstab.mountrecover.bak /etc/fstab ; systemctl daemon-reload ; reboot'
[[ -f "$marker" ]] || fail 'fake mount validation was not called'

expected_fstab="$scratch/expected.fstab"
cat >"$expected_fstab" <<'EOF'
# protected test table
UUID=11111111-2222-3333-4444-555555555555 / ext4 defaults 0 1
UUID=7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9 /srv/archive ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2
EOF
cmp -s "$expected_fstab" "$late_out/corrected.fstab" || fail 'corrected fstab changed fields beyond the bad options'

healthy_case="$scratch/healthy-late-device"
healthy_out="$scratch/healthy-late-output"
mkdir -p "$healthy_case" "$healthy_out"
cat >"$healthy_case/fstab" <<'EOF'
UUID=7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9 /srv/archive ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2
EOF
cat >"$healthy_case/device-identity.txt" <<'EOF'
/dev/nvme1n1p1: LABEL="archive" UUID="7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9" BLOCK_SIZE="4096" TYPE="ext4"
EOF
cat >"$healthy_case/filesystem-state.txt" <<'EOF'
/dev/nvme1n1p1: clean, 48192/61054976 files, 9031181/244190208 blocks
EOF
cat >"$healthy_case/boot.log" <<'EOF'
Jul 18 03:14:06 host systemd[1]: Timed out waiting for device /dev/disk/by-uuid/7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9.
Jul 18 03:17:42 host kernel: nvme nvme1: device is now ready
EOF

PATH="$project_dir/tests/mockbin:$PATH" \
MOCK_MOUNT_MARKER="$scratch/healthy-must-not-mount" \
    "$project_dir/mountrecover" "$healthy_case" /srv/archive "$healthy_out"
assert_report_line "$healthy_out/report.env" 'STATUS=HEALTHY'
assert_report_line "$healthy_out/report.env" 'REASON=current identity, filesystem state, and mount options are consistent'
assert_report_line "$healthy_out/report.env" 'DEVICE_PRESENT=yes'
assert_report_line "$healthy_out/report.env" 'EARLY_DEVICE_TIMEOUT=yes'
assert_report_line "$healthy_out/report.env" 'VALIDATION=not-run'
[[ ! -e "$healthy_out/corrected.fstab" ]] || fail 'healthy entry must not be rewritten'
[[ ! -e "$scratch/healthy-must-not-mount" ]] || fail 'healthy entry must not invoke mount validation'

missing_case="$scratch/missing-device"
missing_out="$scratch/missing-output"
mkdir -p "$missing_case" "$missing_out"
make_common_fstab "$missing_case"
: >"$missing_case/device-identity.txt"
: >"$missing_case/filesystem-state.txt"
cat >"$missing_case/boot.log" <<'EOF'
Jul 18 03:14:06 host systemd[1]: Reached target Local File Systems.
EOF

PATH="$project_dir/tests/mockbin:$PATH" \
MOCK_MOUNT_MARKER="$scratch/must-not-exist" \
    "$project_dir/mountrecover" "$missing_case" /srv/archive "$missing_out"
assert_report_line "$missing_out/report.env" 'STATUS=MISSING_DEVICE'
assert_report_line "$missing_out/report.env" 'DEVICE_PRESENT=no'
assert_report_line "$missing_out/report.env" 'EARLY_DEVICE_TIMEOUT=no'
assert_report_line "$missing_out/report.env" 'VALIDATION=not-run'
[[ ! -e "$missing_out/corrected.fstab" ]] || fail 'missing device must not produce an option-only correction'
[[ ! -e "$scratch/must-not-exist" ]] || fail 'missing device must not invoke mount validation'

dirty_case="$scratch/dirty-filesystem"
dirty_out="$scratch/dirty-output"
mkdir -p "$dirty_case" "$dirty_out"
make_common_fstab "$dirty_case"
cat >"$dirty_case/device-identity.txt" <<'EOF'
/dev/nvme1n1p1: LABEL="archive" UUID="7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9" BLOCK_SIZE="4096" TYPE="ext4"
EOF
cat >"$dirty_case/filesystem-state.txt" <<'EOF'
/dev/nvme1n1p1: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
EOF
cat >"$dirty_case/boot.log" <<'EOF'
Jul 18 03:18:09 host kernel: EXT4-fs (nvme1n1p1): Unrecognized mount option "uid=1000" or missing value
EOF

PATH="$project_dir/tests/mockbin:$PATH" \
MOCK_MOUNT_MARKER="$scratch/dirty-must-not-mount" \
    "$project_dir/mountrecover" "$dirty_case" /srv/archive "$dirty_out"
assert_report_line "$dirty_out/report.env" 'STATUS=FILESYSTEM_NEEDS_CHECK'
assert_report_line "$dirty_out/report.env" 'FILESYSTEM_STATE=needs_check'
assert_report_line "$dirty_out/report.env" 'VALIDATION=not-run'
[[ ! -e "$dirty_out/corrected.fstab" ]] || fail 'dirty filesystem must not produce an option-only correction'
[[ ! -e "$scratch/dirty-must-not-mount" ]] || fail 'dirty filesystem must not invoke mount validation'

type_case="$scratch/type-mismatch"
type_out="$scratch/type-output"
mkdir -p "$type_case" "$type_out"
make_common_fstab "$type_case"
cat >"$type_case/device-identity.txt" <<'EOF'
/dev/nvme1n1p1: LABEL="archive" UUID="7c2a11ef-51b8-4d03-b8d3-fc08ecad31a9" TYPE="xfs"
EOF
cat >"$type_case/filesystem-state.txt" <<'EOF'
/dev/nvme1n1p1: clean
EOF
: >"$type_case/boot.log"

PATH="$project_dir/tests/mockbin:$PATH" \
MOCK_MOUNT_MARKER="$scratch/type-must-not-mount" \
    "$project_dir/mountrecover" "$type_case" /srv/archive "$type_out"
assert_report_line "$type_out/report.env" 'STATUS=FILESYSTEM_TYPE_MISMATCH'
assert_report_line "$type_out/report.env" 'EXPECTED_FILESYSTEM=ext4'
assert_report_line "$type_out/report.env" 'ACTUAL_FILESYSTEM=xfs'
assert_report_line "$type_out/report.env" 'VALIDATION=not-run'
[[ ! -e "$type_out/corrected.fstab" ]] || fail 'filesystem mismatch must not produce an option-only correction'
[[ ! -e "$scratch/type-must-not-mount" ]] || fail 'filesystem mismatch must not invoke mount validation'

echo 'PASS: mount recovery diagnosis, correction, validation, and reversal contract'
