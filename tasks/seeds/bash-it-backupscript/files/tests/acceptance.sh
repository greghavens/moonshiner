#!/usr/bin/env bash
set -uo pipefail
unset BACKUP_ALLOWLIST BACKUP_TIMESTAMP BACKUP_KEEP BACKUP_RESTORE_ROOT

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT=$ROOT/backup.sh
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/local-backup-acceptance.XXXXXX")
trap 'rm -rf -- "$TEST_ROOT"' EXIT
failures=0
tests=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  return 1
}

run() {
  local name=$1
  shift
  ((tests += 1))
  (set -euo pipefail; "$@")
  local status=$?
  if ((status == 0)); then
    printf 'ok %d - %s\n' "$tests" "$name"
  else
    printf 'not ok %d - %s\n' "$tests" "$name"
    ((failures += 1))
  fi
}

fixture() {
  local name=$1
  CASE=$TEST_ROOT/$name
  ALLOWED=$CASE/allowed
  SOURCE=$ALLOWED/project
  OUTSIDE=$CASE/outside
  DEST=$CASE/backups
  ALLOWLIST=$CASE/allowlist
  mkdir -p "$SOURCE/sub" "$OUTSIDE"
  printf '%s\n' "$ALLOWED" > "$ALLOWLIST"
}

test_backup_restore_and_instruction() {
  fixture basic
  printf 'alpha\n' > "$SOURCE/sub/a file.txt"
  local restore=$CASE/restore output snapshot expected quoted
  output=$(BACKUP_ALLOWLIST="$ALLOWLIST" \
    BACKUP_TIMESTAMP=20250101T010101Z BACKUP_RESTORE_ROOT="$restore" \
    "$SCRIPT" backup "$DEST" "$SOURCE")
  snapshot=$DEST/snapshots/20250101T010101Z
  [[ $(readlink "$DEST/latest") == snapshots/20250101T010101Z ]]
  (cd "$snapshot" && sha256sum --status -c manifest.sha256)
  "$SCRIPT" verify "$snapshot" >/dev/null
  expected='Restore with:'
  printf -v quoted ' %q' "$SCRIPT" restore "$snapshot" "$restore"
  grep -Fqx -- "$expected$quoted" <<< "$output"
  "$SCRIPT" restore "$snapshot" "$restore" >/dev/null
  cmp "$SOURCE/sub/a file.txt" "$restore/${SOURCE#/}/sub/a file.txt"
}

test_incremental_manifest_and_hardlinks() {
  fixture incremental
  printf 'same\n' > "$SOURCE/sub/stable.txt"
  printf 'one\n' > "$SOURCE/changing.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  printf 'two\n' > "$SOURCE/changing.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000002Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  local rel=${SOURCE#/}
  local one=$DEST/snapshots/20250101T000001Z/payload/$rel
  local two=$DEST/snapshots/20250101T000002Z/payload/$rel
  [[ $(stat -c '%i' "$one/sub/stable.txt") == $(stat -c '%i' "$two/sub/stable.txt") ]]
  [[ $(stat -c '%i' "$one/changing.txt") != $(stat -c '%i' "$two/changing.txt") ]]
  grep -Fq $'F\t' "$DEST/snapshots/20250101T000002Z/manifest.tsv"

  printf 'corrupt\n' > "$two/sub/stable.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000003Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1
  local three=$DEST/snapshots/20250101T000003Z/payload/$rel
  cmp "$SOURCE/sub/stable.txt" "$three/sub/stable.txt"
  "$SCRIPT" verify "$DEST/snapshots/20250101T000003Z" >/dev/null
}

test_retention() {
  fixture retention
  printf 'data\n' > "$SOURCE/item"
  local stamp
  for stamp in 20250101T000001Z 20250101T000002Z 20250101T000003Z; do
    BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=$stamp BACKUP_KEEP=2 \
      "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  done
  [[ ! -e "$DEST/snapshots/20250101T000001Z" ]]
  [[ -d "$DEST/snapshots/20250101T000002Z" ]]
  [[ -d "$DEST/snapshots/20250101T000003Z" ]]
  [[ $(find "$DEST/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l) == 2 ]]

  local collision_dest=$CASE/collision-backups
  for stamp in 1 2 3; do
    BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250102T000000Z BACKUP_KEEP=2 \
      "$SCRIPT" backup "$collision_dest" "$SOURCE" >/dev/null
  done
  [[ ! -e "$collision_dest/snapshots/20250102T000000Z" ]]
  [[ -d "$collision_dest/snapshots/20250102T000000Z.1" ]]
  [[ -d "$collision_dest/snapshots/20250102T000000Z.2" ]]
}

test_partial_failure_is_not_published() {
  fixture partial
  printf 'good\n' > "$SOURCE/good.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  local old_latest
  old_latest=$(readlink "$DEST/latest")
  mkfifo "$SOURCE/unsupported.pipe"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000002Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1; then
    fail 'backup with an unsupported file unexpectedly succeeded'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000002Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .
}

test_lock_refuses_concurrent_writer() {
  fixture locking
  printf 'good\n' > "$SOURCE/good.txt"
  mkdir -p "$DEST/.backup.lock"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1; then
    fail 'backup ignored an existing lock'
  fi
  [[ -d "$DEST/.backup.lock" ]]
  [[ ! -e "$DEST/latest" ]]
}

test_checksum_blocks_corrupt_restore() {
  fixture checksum
  printf 'original\n' > "$SOURCE/good.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  local snapshot=$DEST/snapshots/20250101T000001Z
  printf 'tampered\n' > "$snapshot/payload/${SOURCE#/}/good.txt"
  if "$SCRIPT" restore "$snapshot" "$CASE/restore" >/dev/null 2>&1; then
    fail 'restore accepted corrupt payload data'
  fi
  [[ ! -e "$CASE/restore/${SOURCE#/}/good.txt" ]]
}

test_allowlist_boundary_and_internal_symlink() {
  fixture boundaries
  mkdir -p "$CASE/allowed-other"
  printf 'outside\n' > "$CASE/allowed-other/no.txt"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$CASE/allowed-other" >/dev/null 2>&1; then
    fail 'component-prefix path bypassed the allowlist'
  fi
  printf 'inside\n' > "$SOURCE/sub/inside.txt"
  ln -s sub/inside.txt "$SOURCE/inside-link"
  local second_root=$CASE/second-root
  mkdir -p "$second_root"
  printf 'second root\n' > "$second_root/other.txt"
  printf '%s\n' "$second_root" >> "$ALLOWLIST"
  ln -s "$second_root/other.txt" "$SOURCE/cross-root-link"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000002Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  [[ -L "$DEST/snapshots/20250101T000002Z/payload/${SOURCE#/}/inside-link" ]]
  [[ -L "$DEST/snapshots/20250101T000002Z/payload/${SOURCE#/}/cross-root-link" ]]

  ln -s "$second_root" "$ALLOWED/requested-internal"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000003Z \
    "$SCRIPT" backup "$CASE/internal-link-backups" "$ALLOWED/requested-internal" >/dev/null
  "$SCRIPT" verify \
    "$CASE/internal-link-backups/snapshots/20250101T000003Z" >/dev/null
}

test_symlink_escape_is_rejected_atomically() {
  fixture symlink_escape
  printf 'safe\n' > "$SOURCE/safe.txt"
  printf 'secret\n' > "$OUTSIDE/secret.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  local old_latest
  old_latest=$(readlink "$DEST/latest")

  ln -s "$OUTSIDE/secret.txt" "$SOURCE/escape"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000002Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1; then
    fail 'nested symlink escape was published'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000002Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .

  rm "$SOURCE/escape"
  ln -s "$OUTSIDE" "$ALLOWED/requested-link"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000003Z \
    "$SCRIPT" backup "$DEST" "$ALLOWED/requested-link" >/dev/null 2>&1; then
    fail 'explicit source symlink escape was published'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000003Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .
}

test_chained_and_dangling_symlinks_are_rejected() {
  fixture symlink_resolution
  printf 'safe\n' > "$SOURCE/safe.txt"
  printf 'secret\n' > "$OUTSIDE/secret.txt"
  BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000001Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null
  local old_latest
  old_latest=$(readlink "$DEST/latest")

  # The first hop appears to remain in the allowlisted root, but resolving the
  # complete chain reaches the outside directory.
  ln -s "$OUTSIDE" "$ALLOWED/bridge"
  ln -s "$ALLOWED/bridge/secret.txt" "$SOURCE/chained-escape"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000002Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1; then
    fail 'nested symlink chain escaping the allowlist was published'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000002Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .

  rm "$SOURCE/chained-escape" "$ALLOWED/bridge"
  ln -s "$OUTSIDE/missing" "$SOURCE/dangling"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000003Z \
    "$SCRIPT" backup "$DEST" "$SOURCE" >/dev/null 2>&1; then
    fail 'nested dangling symlink was published'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000003Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .

  rm "$SOURCE/dangling"
  ln -s "$OUTSIDE/missing" "$ALLOWED/requested-dangling"
  if BACKUP_ALLOWLIST="$ALLOWLIST" BACKUP_TIMESTAMP=20250101T000004Z \
    "$SCRIPT" backup "$DEST" "$ALLOWED/requested-dangling" >/dev/null 2>&1; then
    fail 'explicit dangling source symlink was published'
  fi
  [[ $(readlink "$DEST/latest") == "$old_latest" ]]
  [[ ! -e "$DEST/snapshots/20250101T000004Z" ]]
  ! find "$DEST" -maxdepth 1 -name '.staging.*' | grep -q .
}

run 'backup, exact instruction, verification, and restore' test_backup_restore_and_instruction
run 'incremental manifest and unchanged hard links' test_incremental_manifest_and_hardlinks
run 'retention keeps the newest configured snapshots' test_retention
run 'partial failures do not publish staging data' test_partial_failure_is_not_published
run 'the destination lock refuses another writer' test_lock_refuses_concurrent_writer
run 'payload checksum failure blocks restore' test_checksum_blocks_corrupt_restore
run 'allowlist component boundaries and internal symlinks' test_allowlist_boundary_and_internal_symlink
run 'symlink escapes are rejected without publication' test_symlink_escape_is_rejected_atomically
run 'symlink chains and dangling links are rejected' test_chained_and_dangling_symlinks_are_rejected

if ((failures != 0)); then
  printf '%d of %d tests failed\n' "$failures" "$tests" >&2
  exit 1
fi
printf 'all %d tests passed\n' "$tests"
