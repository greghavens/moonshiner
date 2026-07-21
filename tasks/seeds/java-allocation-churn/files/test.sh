#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 -B -m unittest discover -s "$project_dir/tests" -p 'test_*.py' -v

if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
  build_dir=$(mktemp -d "${TMPDIR:-/tmp}/java-allocation-churn.XXXXXXXX")
  trap 'rm -rf -- "$build_dir"' EXIT

  find "$project_dir/src/main/java" "$project_dir/src/test/java" \
    -type f -name '*.java' -print | LC_ALL=C sort > "$build_dir/sources.txt"

  mkdir -p "$build_dir/classes"
  javac --release 17 -encoding UTF-8 -d "$build_dir/classes" \
    @"$build_dir/sources.txt"
  java -cp "$build_dir/classes" com.moonshiner.telemetry.TelemetryParserTest
else
  echo "JDK unavailable; protected source-contract checks passed."
fi
