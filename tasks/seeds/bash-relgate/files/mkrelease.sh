#!/usr/bin/env bash
# mkrelease.sh — stage a release bundle out of a finished build tree.
#
# Build tree contract (produced by `make dist-tree`):
#   VERSION            single line: the release version
#   MANIFEST           one artifact per line: <path><TAB><max-kb>
#   <path>             the artifact files themselves (relative, no whitespace)
#
# Produces <outdir>/rel-<version>/ holding every artifact plus RECEIPT
# (one line per artifact: <path><TAB><cksum-crc><TAB><bytes>).
set -eu
LC_ALL=C
export LC_ALL

log() { printf 'mkrelease: %s\n' "$1"; }

usage() {
  printf 'usage: mkrelease.sh <builddir> <outdir>\n' >&2
  exit 64
}

[ "$#" -eq 2 ] || usage
build=$1
out=$2
[ -d "$build" ] || { printf 'mkrelease: not a directory: %s\n' "$build" >&2; exit 66; }

version=''
stage=''
receipt=''

read_version() {
  local v=$(cat "$build/VERSION" 2>/dev/null)
  version=$v
  log "version $version"
}

stage_artifacts() {
  mkdir -p "$stage"
  while IFS=$'\t' read -r path maxkb; do
    [ -n "$path" ] || continue
    mkdir -p "$stage/$(dirname "$path")"
    cp "$build/$path" "$stage/$path" 2>/dev/null
    log "staged $path"
  done < "$build/MANIFEST"
  log "stage populated"
}

check_budget() { # <path> <bytes> <max-kb>
  if [ "$2" -gt "$(($3 * 1024))" ]; then
    printf 'mkrelease: over budget: %s (%s > %s)\n' "$1" "$2" "$(($3 * 1024))" >&2
    return 1
  fi
  return 0
}

write_receipt() {
  : > "$receipt"
  while IFS=$'\t' read -r path maxkb; do
    [ -n "$path" ] || continue
    set -- $(cksum "$stage/$path" 2>/dev/null)
    crc=$1
    bytes=$2
    check_budget "$path" "$bytes" "$maxkb"
    printf '%s\t%s\t%s\n' "$path" "$crc" "$bytes" >> "$receipt"
  done < "$build/MANIFEST"
  log "receipt written"
}

read_version
stage="$out/rel-$version"
receipt="$stage/RECEIPT"

if stage_artifacts; then
  log "artifacts in place"
else
  printf 'mkrelease: staging failed\n' >&2
  exit 70
fi

write_receipt && log "sealed"

log "release OK: rel-$version"
