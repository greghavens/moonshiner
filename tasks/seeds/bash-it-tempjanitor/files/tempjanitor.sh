#!/usr/bin/env bash
# Reclaim old entries from a job-owned temporary directory without crossing
# ownership, filesystem, symlink, lock, or open-file safety boundaries.
set -u
LC_ALL=C
export LC_ALL

usage() {
  printf 'usage: tempjanitor.sh [--dry-run] --root DIR --age SECONDS --owner UID --lock FILE --now EPOCH\n' >&2
}

usage_error() {
  usage
  exit 64
}

dry_run=false
root=''
age=''
owner_uid=''
lock_file=''
now=''

while (($#)); do
  case $1 in
    --dry-run)
      dry_run=true
      shift
      ;;
    --root|--age|--owner|--lock|--now)
      (($# >= 2)) || usage_error
      case $1 in
        --root) root=$2 ;;
        --age) age=$2 ;;
        --owner) owner_uid=$2 ;;
        --lock) lock_file=$2 ;;
        --now) now=$2 ;;
      esac
      shift 2
      ;;
    *)
      usage_error
      ;;
  esac
done

[[ -n $root && -n $age && -n $owner_uid && -n $lock_file && -n $now ]] || usage_error
[[ $age =~ ^[0-9]+$ && $owner_uid =~ ^[0-9]+$ && $now =~ ^[0-9]+$ ]] || usage_error

# Bash arithmetic treats a leading zero as an octal prefix.  Normalize the
# accepted decimal spellings before doing arithmetic or comparing an owner ID.
normalize_uint() {
  local value=$1
  while [[ ${#value} -gt 1 && ${value:0:1} == 0 ]]; do
    value=${value:1}
  done
  printf '%s\n' "$value"
}

age=$(normalize_uint "$age")
owner_uid=$(normalize_uint "$owner_uid")
now=$(normalize_uint "$now")

while [[ $root == */ && $root != / ]]; do
  root=${root%/}
done
if [[ ! -d $root || -L $root ]]; then
  printf 'tempjanitor.sh: unsafe root: %s\n' "$root" >&2
  exit 64
fi
if ! root=$(cd -P -- "$root" && pwd -P); then
  printf 'tempjanitor.sh: cannot resolve root: %s\n' "$root" >&2
  exit 1
fi
if [[ $root == / ]]; then
  printf 'tempjanitor.sh: unsafe root: /\n' >&2
  exit 64
fi

if ! root_device=$(stat -c '%d' -- "$root" 2>/dev/null); then
  printf 'tempjanitor.sh: cannot stat root: %s\n' "$root" >&2
  exit 1
fi

if ! exec {lock_fd}>>"$lock_file"; then
  printf 'tempjanitor.sh: cannot open lock: %s\n' "$lock_file" >&2
  exit 1
fi
if ! flock -n "$lock_fd"; then
  printf 'tempjanitor.sh: lock busy: %s\n' "$lock_file" >&2
  exit 75
fi

# Print one shell string as a JSON string, including escaping every ASCII
# control byte that can occur in a pathname (NUL cannot occur in pathnames).
json_string() {
  local value=$1 char code i
  printf '"'
  for ((i = 0; i < ${#value}; i++)); do
    char=${value:i:1}
    case $char in
      '"') printf '\\"' ;;
      $'\\') printf '\\\\' ;;
      $'\b') printf '\\b' ;;
      $'\f') printf '\\f' ;;
      $'\n') printf '\\n' ;;
      $'\r') printf '\\r' ;;
      $'\t') printf '\\t' ;;
      *)
        printf -v code '%d' "'$char"
        if ((code < 32)); then
          printf '\\u%04x' "$code"
        else
          printf '%s' "$char"
        fi
        ;;
    esac
  done
  printf '"'
}

emit_skip() {
  printf '{"event":"candidate","path":'
  json_string "$1"
  printf ',"action":"skip","reason":"%s"}\n' "$2"
}

emit_eligible() {
  printf '{"event":"candidate","path":'
  json_string "$1"
  printf ',"action":"%s","reason":"eligible","bytes":%s}\n' "$2" "$3"
}

same_filesystem_tree() {
  local candidate=$1 device saw=0
  while IFS= read -r -d '' device; do
    saw=1
    [[ $device == "$root_device" ]] || return 1
  done < <(find -P "$candidate" -xdev -printf '%D\0' 2>/dev/null)
  ((saw == 1))
}

owned_tree() {
  local candidate=$1 uid saw=0
  while IFS= read -r -d '' uid; do
    saw=1
    [[ $uid == "$owner_uid" ]] || return 1
  done < <(find -P "$candidate" -xdev -printf '%U\0' 2>/dev/null)
  ((saw == 1))
}

is_open_candidate() {
  local candidate=$1 fd target
  for fd in /proc/[0-9]*/fd/*; do
    [[ -L $fd ]] || continue
    # Append a sentinel so command substitution preserves any newline bytes at
    # the end of the target pathname, then remove exactly that sentinel.
    target=$(readlink -n -- "$fd" 2>/dev/null && printf .) || continue
    target=${target%.}
    # Test the unmodified target first: an existing pathname may itself end in
    # the marker that procfs appends to a target after it has been unlinked.
    [[ $target == "$candidate" ]] && return 0
    target=${target%" (deleted)"}
    [[ $target == "$candidate" ]] && return 0
  done
  return 1
}

candidate_bytes() {
  local candidate=$1 size total=0
  if [[ -f $candidate ]]; then
    stat -c '%s' -- "$candidate"
    return
  fi
  if [[ -d $candidate ]]; then
    while IFS= read -r -d '' size; do
      total=$((total + size))
    done < <(find -P "$candidate" -xdev -type f -printf '%s\0')
  fi
  printf '%s\n' "$total"
}

delete_candidate() {
  local candidate=$1
  if [[ -d $candidate ]]; then
    find -P "$candidate" -xdev -depth -delete
  else
    rm -f -- "$candidate"
  fi
}

cutoff=$((now - age))
removed=0
would_remove=0
reclaimed_bytes=0
eligible_bytes=0
skipped=0
errors=0

while IFS= read -r -d '' candidate; do
  name=${candidate#"$root"/}

  if [[ -L $candidate ]]; then
    emit_skip "$name" symlink
    skipped=$((skipped + 1))
    continue
  fi

  if ! IFS=' ' read -r device uid mtime < <(stat -c '%d %u %Y' -- "$candidate" 2>/dev/null); then
    printf 'tempjanitor.sh: candidate vanished: %s\n' "$candidate" >&2
    errors=1
    continue
  fi
  if [[ $device != "$root_device" ]] || ! same_filesystem_tree "$candidate"; then
    emit_skip "$name" filesystem
    skipped=$((skipped + 1))
    continue
  fi
  if [[ $uid != "$owner_uid" ]] || ! owned_tree "$candidate"; then
    emit_skip "$name" owner
    skipped=$((skipped + 1))
    continue
  fi
  if ((mtime > cutoff)); then
    emit_skip "$name" young
    skipped=$((skipped + 1))
    continue
  fi
  if is_open_candidate "$candidate"; then
    emit_skip "$name" open
    skipped=$((skipped + 1))
    continue
  fi
  if ! bytes=$(candidate_bytes "$candidate"); then
    printf 'tempjanitor.sh: cannot measure candidate: %s\n' "$candidate" >&2
    errors=1
    continue
  fi

  if $dry_run; then
    emit_eligible "$name" would_remove "$bytes"
    would_remove=$((would_remove + 1))
    eligible_bytes=$((eligible_bytes + bytes))
  elif delete_candidate "$candidate"; then
    emit_eligible "$name" removed "$bytes"
    removed=$((removed + 1))
    reclaimed_bytes=$((reclaimed_bytes + bytes))
  else
    printf 'tempjanitor.sh: cannot remove candidate: %s\n' "$candidate" >&2
    errors=1
  fi
done < <(find -P "$root" -xdev -mindepth 1 -maxdepth 1 -print0 | sort -z)

printf '{"event":"summary","dry_run":%s,"removed":%d,"would_remove":%d,"reclaimed_bytes":%d,"eligible_bytes":%d,"skipped":%d}\n' \
  "$dry_run" "$removed" "$would_remove" "$reclaimed_bytes" "$eligible_bytes" "$skipped"

((errors == 0))
