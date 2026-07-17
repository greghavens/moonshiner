#!/usr/bin/env bash
# snapshot.sh -- file the month's export drop into a labelled snapshot folder.
# Copies the loose files, writes MANIFEST.tsv, then re-checks that every
# source file actually made it across before declaring victory.
set -u
LC_ALL=C
export LC_ALL

if [ $# -ne 3 ]; then
  echo "usage: snapshot.sh <source-dir> <snapshot-root> <label>" >&2
  exit 2
fi

src=$1
root=$2
label=$3

if [ ! -d "$src" ]; then
  echo "snapshot: source directory not found: $src" >&2
  exit 2
fi

snap="$root/$label"
if [ -e "$snap" ]; then
  echo "snapshot: already exists, refusing to overwrite: $snap" >&2
  exit 2
fi
mkdir -p "$snap"

# copy the loose files; subdirectories belong to other teams and stay behind
copied=0
for f in "$src"/*; do
  [ -f "$f" ] || continue
  if cp -p $f "$snap/"; then
    copied=$((copied + 1))
  fi
done

# manifest: <name><TAB><bytes>, one line per file, sorted
manifest="$snap/MANIFEST.tsv"
: > "$manifest"
for f in "$snap"/*; do
  name=${f##*/}
  [ "$name" = MANIFEST.tsv ] && continue
  bytes=$(wc -c < $f)
  printf '%s\t%s\n' "$name" "$bytes" >> "$manifest"
done
sort -o "$manifest" "$manifest"

# paranoia pass: every loose source file must be present in the snapshot
missing=0
for f in "$src"/*; do
  [ -f "$f" ] || continue
  name=${f##*/}
  [ -f $snap/$name ] || {
    echo "snapshot: missing from snapshot: $name" >&2
    missing=$((missing + 1))
  }
done

echo "copied $copied file(s) into $snap"
if [ "$missing" -gt 0 ]; then
  echo "verify FAILED: $missing file(s) did not make it" >&2
  exit 1
fi
echo "verify ok"
