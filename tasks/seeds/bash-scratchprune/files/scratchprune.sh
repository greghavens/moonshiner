#!/usr/bin/env bash
# scratchprune.sh — retire finished project directories from the lab scratch
# share and flush the shared spool. Run nightly from cron on the head node.
#
# usage: scratchprune.sh SCRATCH_ROOT
#
# SCRATCH_ROOT holds one directory per project plus three control files:
#   retire.list   project names whose directories can go
#   keep.list     projects pinned by the lab leads (overrides retire.list)
#   prune.conf    key=value settings; spool= names the shared spool directory

if [ $# -ne 1 ]; then
  echo "usage: scratchprune.sh SCRATCH_ROOT" >&2
  exit 2
fi

root="$1"
conf_file="$root/prune.conf"
keep_file="$root/keep.list"
retire_file="$root/retire.list"

read_conf() {
  grep "^${1}=" "$conf_file" 2>/dev/null | head -n 1 | cut -d= -f2-
}

line_count() {
  local n=$(wc -l < "$1")
  printf '%s' "${n// /}"
}

is_kept() {
  grep -qx "$1" "$keep_file" 2>/dev/null
  if [ $? -eq 0 ]; then
    return 0
  fi
  return 1
}

usage_kb() {
  local dir="$1"
  local kb=$(du -sk "$dir" 2>/dev/null)
  if [ $? -ne 0 ]; then
    return 1
  fi
  printf '%s' "${kb%%[[:space:]]*}"
}

retired=0
kept=0
skipped=0

printf 'queued %s project(s)\n' "$(line_count "$retire_file")"

while IFS= read -r proj; do
  [ -n "$proj" ] || continue
  if is_kept "$proj"; then
    printf 'keeping %s\n' "$proj"
    kept=$((kept + 1))
    continue
  fi
  if kb=$(usage_kb "$root/$proj"); then
    rm -rf "$root/$proj"
    printf 'retired %s %sKB\n' "$proj" "$kb"
    retired=$((retired + 1))
  else
    printf 'warning: cannot size %s, skipped\n' "$proj" >&2
    skipped=$((skipped + 1))
  fi
done < "$retire_file"

spool_dir=$(read_conf spool)
spool_files=$(find "$spool_dir" -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null | wc -l)
rm -rf "$spool_dir/"*
printf 'spool flushed %s entries\n' "${spool_files// /}"

printf 'done: %s retired, %s kept, %s skipped\n' "$retired" "$kept" "$skipped"
if [ "$skipped" -gt 0 ]; then
  exit 3
fi
exit 0
