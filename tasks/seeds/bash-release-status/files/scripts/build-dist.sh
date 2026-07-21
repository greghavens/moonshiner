#!/usr/bin/env bash
# Deterministic local distribution builder.
set -u
LC_ALL=C
export LC_ALL

if [[ $# -ne 2 ]]; then
  printf 'build: usage: build-dist.sh <version> <artifact>\n' >&2
  exit 64
fi

version=$1
artifact=$2
source_dir=${RELEASE_SOURCE_DIR:-src}

shopt -s nullglob
sources=("$source_dir"/*.txt)
if [[ ${#sources[@]} -eq 0 ]]; then
  printf 'build: error: no source files in %s\n' "$source_dir" >&2
  exit 22
fi

mkdir -p -- "${artifact%/*}"
printf 'package=%s\n' "$version" > "$artifact"
for source in "${sources[@]}"; do
  relative=${source#"$source_dir"/}
  printf 'build: packaging %s\n' "$relative"
  printf 'file=%s\n' "$relative" >> "$artifact"
  if [[ ! -s $source ]]; then
    printf 'build: error: empty source file: %s\n' "$source" >&2
    exit 23
  fi
  cat -- "$source" >> "$artifact"
done

printf 'build: wrote %s\n' "$artifact"
