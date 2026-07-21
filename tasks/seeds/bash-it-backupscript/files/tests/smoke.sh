#!/usr/bin/env bash
set -euo pipefail
unset BACKUP_ALLOWLIST BACKUP_TIMESTAMP BACKUP_KEEP BACKUP_RESTORE_ROOT

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CASE=$(mktemp -d "${TMPDIR:-/tmp}/local-backup-smoke.XXXXXX")
trap 'rm -rf -- "$CASE"' EXIT

mkdir -p "$CASE/allowed/project"
printf 'hello\n' > "$CASE/allowed/project/hello.txt"
printf '%s\n' "$CASE/allowed" > "$CASE/allowlist"

output=$(BACKUP_ALLOWLIST="$CASE/allowlist" \
  BACKUP_TIMESTAMP=20250101T000000Z \
  BACKUP_RESTORE_ROOT="$CASE/restored" \
  "$ROOT/backup.sh" backup "$CASE/backups" "$CASE/allowed/project")

snapshot=$CASE/backups/snapshots/20250101T000000Z
[[ -d "$snapshot" ]]
(cd "$snapshot" && sha256sum --status -c manifest.sha256)
"$ROOT/backup.sh" verify "$snapshot" >/dev/null

expected='Restore with:'
printf -v quoted ' %q' "$ROOT/backup.sh" restore "$snapshot" "$CASE/restored"
grep -Fqx -- "$expected$quoted" <<< "$output"

"$ROOT/backup.sh" restore "$snapshot" "$CASE/restored" >/dev/null
cmp "$CASE/allowed/project/hello.txt" \
  "$CASE/restored/${CASE#/}/allowed/project/hello.txt"

printf 'smoke: ok\n'
