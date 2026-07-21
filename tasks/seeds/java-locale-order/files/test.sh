#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v javac >/dev/null 2>&1 \
    || ! command -v java >/dev/null 2>&1; then
  python3 -B -m unittest discover \
    -s "$project_dir/tests" -p 'test_*.py' -v
  exit
fi

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/java-locale-order.XXXXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT

mapfile -t sources < <(
  find \
    "$project_dir/src/main/java" \
    "$project_dir/src/testFixtures/java" \
    "$project_dir/src/test/java" \
    -type f -name '*.java' -print | LC_ALL=C sort
)

mkdir -p "$build_dir/classes"
javac --release 17 -encoding UTF-8 -Xlint:all -Werror \
  -d "$build_dir/classes" "${sources[@]}"

jvm_defaults=(
  -Duser.language=en
  -Duser.country=US
  -Duser.timezone=UTC
)

case "${1:-}" in
  "")
    if (( $# != 0 )); then
      echo "usage: bash test.sh [direct|seeded SEED]" >&2
      exit 2
    fi
    java "${jvm_defaults[@]}" -cp "$build_dir/classes" \
      com.moonshiner.reports.UsReportCase
    java "${jvm_defaults[@]}" -cp "$build_dir/classes" \
      com.moonshiner.reports.SeededTestRunner 10010
    ;;
  direct)
    if (( $# != 1 )); then
      echo "usage: bash test.sh direct" >&2
      exit 2
    fi
    java "${jvm_defaults[@]}" -cp "$build_dir/classes" \
      com.moonshiner.reports.UsReportCase
    ;;
  seeded)
    if (( $# != 2 )); then
      echo "usage: bash test.sh seeded SEED" >&2
      exit 2
    fi
    java "${jvm_defaults[@]}" -cp "$build_dir/classes" \
      com.moonshiner.reports.SeededTestRunner "$2"
    ;;
  *)
    echo "usage: bash test.sh [direct|seeded SEED]" >&2
    exit 2
    ;;
esac
