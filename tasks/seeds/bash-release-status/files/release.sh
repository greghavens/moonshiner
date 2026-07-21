#!/usr/bin/env bash
# Build, checksum, and tag a release while mirroring build output for CI.
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

usage() {
  printf 'usage: release.sh [--dry-run] <version>\n' >&2
  exit 64
}

dry_run=0
if [[ ${1:-} == --dry-run ]]; then
  dry_run=1
  shift
fi
[[ $# -eq 1 && -n $1 ]] || usage
version=$1

case $version in
  *[!0-9A-Za-z._-]*)
    printf 'release: invalid version: %s\n' "$version" >&2
    exit 64
    ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd -- "$script_dir"

name="moonshiner-$version.tar"
artifact="dist/$name"
checksum="$artifact.sha256"
build_log=dist/build.log
tag="v$version"

if [[ $dry_run -eq 1 ]]; then
  printf 'release: dry-run: build %s\n' "$name"
  printf 'release: dry-run: checksum %s.sha256\n' "$name"
  printf 'release: dry-run: tag %s\n' "$tag"
  exit 0
fi

mkdir -p dist
rm -f -- "$artifact" "$checksum"

printf 'release: building %s\n' "$name"
bash scripts/build-dist.sh "$version" "$artifact" 2>&1 | tee "$build_log"
build_rc=$?
if [[ $build_rc -ne 0 ]]; then
  rm -f -- "$artifact" "$checksum"
  printf 'release: build failed (exit %d); release not tagged\n' "$build_rc" >&2
  exit "$build_rc"
fi

if [[ ! -f $artifact ]]; then
  rm -f -- "$checksum"
  printf 'release: build produced no artifact: %s\n' "$artifact" >&2
  exit 70
fi

if ! (cd dist && sha256sum "$name" > "$name.sha256"); then
  rm -f -- "$artifact" "$checksum"
  printf 'release: checksum failed; release not tagged\n' >&2
  exit 74
fi
printf 'release: checksum %s.sha256\n' "$name"

if ! git tag -a "$tag" -m "Release $version"; then
  rm -f -- "$artifact" "$checksum"
  printf 'release: tag failed: %s\n' "$tag" >&2
  exit 75
fi
printf 'release: tagged %s\n' "$tag"
