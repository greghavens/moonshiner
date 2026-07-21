#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

python3 -B -m unittest discover -s tests -p 'test_*.py' -v

if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
    build_dir="$(mktemp -d)"
    trap 'rm -rf -- "$build_dir"' EXIT

    source_list="$build_dir/java-sources.list"
    find src/main/java src/test/java -name '*.java' -print | LC_ALL=C sort > "$source_list"
    javac -encoding UTF-8 -d "$build_dir" @"$source_list"
    java -ea -cp "$build_dir" moonshiner.routes.RouteSpecParserTest
else
    echo "JDK unavailable; protected source-contract checks passed."
fi
