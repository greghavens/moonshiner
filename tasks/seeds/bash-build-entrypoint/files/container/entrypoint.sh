#!/usr/bin/env bash
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RELEASE_ROOT=$(CDPATH= cd -- "$script_dir/.." && pwd)
export RELEASE_ROOT

if [ "$#" -eq 0 ]; then
  printf 'entrypoint: command required\n' >&2
  exit 64
fi

case $1 in
  release)
    shift
    RELEASE_ROUTE=container exec bash "$RELEASE_ROOT/scripts/release.sh" "$@"
    ;;
  *)
    printf 'entrypoint: unknown command: %s\n' "$1" >&2
    exit 64
    ;;
esac
