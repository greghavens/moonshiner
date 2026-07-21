#!/usr/bin/env bash
set -u

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/c-fenv-leak.XXXXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT HUP INT TERM

cc \
  -std=c17 \
  -O2 \
  -Wall \
  -Wextra \
  -Werror \
  -pedantic \
  -frounding-math \
  -fno-builtin-nearbyint \
  -I"$project_dir/include" \
  "$project_dir/src/rounding_scope.c" \
  "$project_dir/tests/protected/fenv_isolation_test.c" \
  -lm \
  -o "$build_dir/fenv_isolation_test" || exit 1

case "${1:-all}" in
  all)
    if (( $# != 0 )); then
      echo "usage: bash test.sh [direct | shuffled 100103 | restoration]" >&2
      exit 2
    fi
    status=0
    "$build_dir/fenv_isolation_test" direct || status=1
    "$build_dir/fenv_isolation_test" shuffled 100103 || status=1
    "$build_dir/fenv_isolation_test" restoration || status=1
    exit "$status"
    ;;
  direct)
    if (( $# != 1 )); then
      echo "usage: bash test.sh direct" >&2
      exit 2
    fi
    "$build_dir/fenv_isolation_test" direct
    exit $?
    ;;
  shuffled)
    if (( $# != 2 )); then
      echo "usage: bash test.sh shuffled 100103" >&2
      exit 2
    fi
    "$build_dir/fenv_isolation_test" shuffled "$2"
    exit $?
    ;;
  restoration)
    if (( $# != 1 )); then
      echo "usage: bash test.sh restoration" >&2
      exit 2
    fi
    "$build_dir/fenv_isolation_test" restoration
    exit $?
    ;;
  *)
    echo "usage: bash test.sh [direct | shuffled 100103 | restoration]" >&2
    exit 2
    ;;
esac
