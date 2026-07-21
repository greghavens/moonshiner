#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
  build_dir=$(mktemp -d "${TMPDIR:-/tmp}/funds-transfer-test.XXXXXXXX")
  trap 'rm -rf -- "$build_dir"' EXIT

  find "$project_dir/src" -type f -name '*.java' -print \
    | LC_ALL=C sort > "$build_dir/sources.txt"
  javac --release 17 -Xlint:all -Werror -d "$build_dir" \
    @"$build_dir/sources.txt"
  java -cp "$build_dir" incident.FundsTransferServiceTest
else
  python3 -B -m unittest discover \
    -s "$project_dir/tests" -p 'test_*.py' -v
fi
