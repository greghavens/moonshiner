#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROGRAM=${0##*/}
readonly SCRIPT_PATH=$(realpath -e -- "$0")

die() {
  printf '%s: %s\n' "$PROGRAM" "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<EOF
Usage:
  $PROGRAM backup DEST SOURCE [SOURCE ...]
  $PROGRAM verify SNAPSHOT
  $PROGRAM restore SNAPSHOT RESTORE_ROOT
EOF
  exit 2
}

require_tools() {
  local tool
  for tool in realpath find sort sha256sum stat cp ln mv awk readlink; do
    command -v "$tool" >/dev/null 2>&1 || die "required command not found: $tool"
  done
}

path_is_within() {
  local candidate=$1 root=$2
  [[ "$candidate" == "$root" || "$candidate" == "$root/"* ]]
}

declare -a ALLOW_ROOTS=()

load_allowlist() {
  local allow_file=${BACKUP_ALLOWLIST:-backup.allowlist}
  local line root

  [[ -f "$allow_file" ]] || die "allowlist is not a regular file: $allow_file"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == /* ]] || die "allowlist entries must be absolute: $line"
    root=$(realpath -e -- "$line") || die "allowlist entry does not exist: $line"
    [[ -d "$root" ]] || die "allowlist entry is not a directory: $line"
    ALLOW_ROOTS+=("${root%/}")
  done < "$allow_file"
  ((${#ALLOW_ROOTS[@]} > 0)) || die "allowlist contains no source roots"
}

authorize_path() {
  local requested=$1 candidate root

  # Normalize spelling before comparing path components. Authorization callers
  # use the returned absolute name for both traversal and snapshot placement.
  candidate=$(realpath -sm -- "$requested") || return 1
  for root in "${ALLOW_ROOTS[@]}"; do
    if path_is_within "$candidate" "$root"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

validate_name() {
  local name=$1
  [[ "$name" != *$'\n'* && "$name" != *$'\t'* ]] ||
    die "tabs and newlines are not supported in source paths"
}

validate_source_tree() {
  local source=$1 entry resolved list_file=$2

  if ! find -P "$source" -print0 > "$list_file"; then
    die "cannot enumerate source: $source"
  fi
  while IFS= read -r -d '' entry; do
    validate_name "$entry"
    if [[ -L "$entry" ]]; then
      if ! resolved=$(authorize_path "$entry"); then
        die "symlink target escapes the source allowlist: $entry"
      fi
      [[ -e "$resolved" ]] || die "symlink target cannot be resolved: $entry"
    elif [[ ! -d "$entry" && ! -f "$entry" ]]; then
      die "unsupported source file type: $entry"
    fi
  done < "$list_file"
}

create_manifest() {
  local snapshot=$1
  local payload=$snapshot/payload
  local entry rel type digest size mode target
  local unsorted=$snapshot/.manifest.unsorted

  : > "$unsorted"
  while IFS= read -r -d '' entry; do
    rel=${entry#"$payload"/}
    validate_name "$rel"
    mode=$(stat -c '%a' -- "$entry") || die "cannot stat snapshot entry: $rel"
    if [[ -L "$entry" ]]; then
      type=L
      target=$(readlink -- "$entry") || die "cannot read symlink: $rel"
      digest=$(printf '%s' "$target" | sha256sum | awk '{print $1}')
      size=${#target}
    elif [[ -f "$entry" ]]; then
      type=F
      digest=$(sha256sum -- "$entry" | awk '{print $1}') ||
        die "cannot checksum snapshot entry: $rel"
      size=$(stat -c '%s' -- "$entry") || die "cannot size snapshot entry: $rel"
    elif [[ -d "$entry" ]]; then
      type=D
      digest=-
      size=0
    else
      die "unsupported file reached snapshot: $rel"
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$type" "$digest" "$size" "$mode" "$rel" >> "$unsorted"
  done < <(find -P "$payload" -mindepth 1 -print0)

  LC_ALL=C sort -t $'\t' -k5,5 -- "$unsorted" > "$snapshot/manifest.tsv"
  rm -f -- "$unsorted"
}

reuse_unchanged_files() {
  local current=$1 previous=$2
  local type digest size mode rel previous_record replacement

  [[ -n "$previous" && -f "$previous/manifest.tsv" ]] || return 0
  # A damaged previous snapshot is never a safe hard-link source. The complete
  # files already copied into the stage remain usable, so skip reuse instead of
  # turning old corruption into a failed or corrupt new backup.
  if ! (verify_snapshot "$previous" >/dev/null 2>&1); then
    printf '%s: warning: previous snapshot failed verification; incremental reuse disabled\n' \
      "$PROGRAM" >&2
    return 0
  fi
  while IFS=$'\t' read -r type digest size mode rel; do
    [[ "$type" == F ]] || continue
    previous_record=$(awk -F '\t' -v p="$rel" '
      $1 == "F" && $5 == p { print $2 "\t" $3 "\t" $4; exit }
    ' "$previous/manifest.tsv")
    [[ "$previous_record" == "$digest"$'\t'"$size"$'\t'"$mode" ]] || continue
    [[ -f "$previous/payload/$rel" && ! -L "$previous/payload/$rel" ]] || continue
    replacement="$current/.reuse.$$"
    ln -- "$previous/payload/$rel" "$replacement" || die "cannot reuse unchanged file: $rel"
    mv -f -- "$replacement" "$current/payload/$rel" || die "cannot install unchanged file: $rel"
  done < "$current/manifest.tsv"
}

verify_snapshot() {
  local requested=$1 snapshot entry type digest size mode rel actual target

  snapshot=$(realpath -e -- "$requested") || die "snapshot does not exist: $requested"
  [[ -d "$snapshot/payload" && -f "$snapshot/manifest.tsv" && -f "$snapshot/manifest.sha256" ]] ||
    die "snapshot is incomplete: $snapshot"
  (cd "$snapshot" && sha256sum --status -c manifest.sha256) ||
    die "manifest checksum verification failed: $snapshot"

  while IFS=$'\t' read -r type digest size mode rel; do
    [[ -n "$rel" && "$rel" != /* && "$rel" != ../* && "$rel" != */../* ]] ||
      die "unsafe path in manifest: $rel"
    entry=$snapshot/payload/$rel
    case "$type" in
      F)
        [[ -f "$entry" && ! -L "$entry" ]] || die "missing regular file: $rel"
        actual=$(sha256sum -- "$entry" | awk '{print $1}')
        [[ "$actual" == "$digest" && $(stat -c '%s' -- "$entry") == "$size" ]] ||
          die "file checksum mismatch: $rel"
        ;;
      L)
        [[ -L "$entry" ]] || die "missing symlink: $rel"
        target=$(readlink -- "$entry")
        actual=$(printf '%s' "$target" | sha256sum | awk '{print $1}')
        [[ "$actual" == "$digest" && ${#target} == "$size" ]] ||
          die "symlink checksum mismatch: $rel"
        ;;
      D)
        [[ -d "$entry" && ! -L "$entry" && "$digest" == - && "$size" == 0 ]] ||
          die "directory mismatch: $rel"
        ;;
      *) die "unknown manifest entry type: $type" ;;
    esac
    [[ $(stat -c '%a' -- "$entry") == "$mode" ]] || die "mode mismatch: $rel"
  done < "$snapshot/manifest.tsv"
  printf '%s\n' "$snapshot"
}

snapshot_id() {
  local stamp=${BACKUP_TIMESTAMP:-}
  if [[ -z "$stamp" ]]; then
    stamp=$(date -u '+%Y%m%dT%H%M%SZ')
  fi
  [[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "invalid BACKUP_TIMESTAMP: $stamp"
  printf '%s\n' "$stamp"
}

backup_command() {
  (($# >= 2)) || usage
  local requested_dest=$1
  shift
  local keep=${BACKUP_KEEP:-5}
  [[ "$keep" =~ ^[1-9][0-9]*$ ]] || die "BACKUP_KEEP must be a positive integer"

  mkdir -p -- "$requested_dest" || die "cannot create backup destination: $requested_dest"
  local dest
  dest=$(realpath -e -- "$requested_dest") || die "cannot resolve backup destination"
  mkdir -p -- "$dest/snapshots"

  load_allowlist

  local -a sources=()
  local requested source other root
  for requested in "$@"; do
    [[ -e "$requested" || -L "$requested" ]] || die "source does not exist: $requested"
    if ! source=$(authorize_path "$requested"); then
      die "source is outside the configured allowlist: $requested"
    fi
    for root in "${ALLOW_ROOTS[@]}"; do
      path_is_within "$dest" "$root" && die "backup destination is inside an allowed source root: $dest"
    done
    for other in "${sources[@]}"; do
      if path_is_within "$source" "$other" || path_is_within "$other" "$source"; then
        die "backup sources overlap: $source and $other"
      fi
    done
    sources+=("$source")
  done

  local lock=$dest/.backup.lock
  mkdir -- "$lock" 2>/dev/null || die "another backup is already running: $lock"
  printf '%s\n' "$$" > "$lock/pid"

  local stage= published=0
  cleanup_backup() {
    local status=$?
    if [[ -n "$stage" && "$published" == 0 && -e "$stage" ]]; then
      rm -rf -- "$stage"
    fi
    rm -rf -- "$lock"
    return "$status"
  }
  trap cleanup_backup EXIT

  local id candidate suffix=0
  id=$(snapshot_id)
  candidate=$id
  while [[ -e "$dest/snapshots/$candidate" ]]; do
    ((suffix += 1))
    candidate=$id.$suffix
  done
  id=$candidate
  stage=$dest/.staging.$id.$$
  mkdir -m 700 -- "$stage" "$stage/payload"

  local list_file=$stage/.source-list rel target
  for source in "${sources[@]}"; do
    validate_source_tree "$source" "$list_file"
    rel=${source#/}
    target=$stage/payload/$rel
    mkdir -p -- "${target%/*}"
    cp -a -- "$source" "$target" || die "failed to copy source: $source"
  done
  rm -f -- "$list_file"

  create_manifest "$stage"
  local previous=
  if [[ -L "$dest/latest" ]]; then
    previous=$(realpath -e -- "$dest/latest" 2>/dev/null || true)
  fi
  reuse_unchanged_files "$stage" "$previous"
  (cd "$stage" && sha256sum manifest.tsv > manifest.sha256)

  local final=$dest/snapshots/$id
  mv -- "$stage" "$final" || die "failed to publish snapshot"
  published=1
  local latest_tmp=$dest/.latest.$$
  ln -s -- "snapshots/$id" "$latest_tmp"
  mv -Tf -- "$latest_tmp" "$dest/latest"

  local -a snapshots=()
  while IFS= read -r candidate; do
    [[ "$candidate" =~ ^[0-9]{8}T[0-9]{6}Z(\.[1-9][0-9]*)?$ ]] &&
      snapshots+=("$candidate")
  done < <(
    find "$dest/snapshots" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
      LC_ALL=C sort -V
  )
  local remove_count=$((${#snapshots[@]} - keep)) index
  if ((remove_count > 0)); then
    for ((index = 0; index < remove_count; index++)); do
      rm -rf -- "$dest/snapshots/${snapshots[index]}" ||
        die "failed to remove expired snapshot: ${snapshots[index]}"
    done
  fi

  local restore_root=${BACKUP_RESTORE_ROOT:-$PWD/restore-$id}
  [[ "$restore_root" == /* ]] || restore_root=$PWD/$restore_root
  printf 'Snapshot: %s\n' "$final"
  printf 'Restore with:'
  printf ' %q' "$SCRIPT_PATH" restore "$final" "$restore_root"
  printf '\n'

  trap - EXIT
  rm -rf -- "$lock"
}

restore_command() {
  (($# == 2)) || usage
  local snapshot
  snapshot=$(verify_snapshot "$1")
  local requested_root=$2
  mkdir -p -- "$requested_root" || die "cannot create restore root: $requested_root"
  local restore_root
  restore_root=$(realpath -e -- "$requested_root") || die "cannot resolve restore root"
  cp -a -- "$snapshot/payload/." "$restore_root/" || die "restore copy failed"
  printf 'Restored: %s -> %s\n' "$snapshot" "$restore_root"
}

main() {
  require_tools
  (($# > 0)) || usage
  local command=$1
  shift
  case "$command" in
    backup) backup_command "$@" ;;
    verify)
      (($# == 1)) || usage
      verify_snapshot "$1" >/dev/null
      printf 'Verified: %s\n' "$(realpath -e -- "$1")"
      ;;
    restore) restore_command "$@" ;;
    *) usage ;;
  esac
}

main "$@"
