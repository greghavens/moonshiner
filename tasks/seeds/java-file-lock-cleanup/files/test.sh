#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
  build_dir=$(mktemp -d "${TMPDIR:-/tmp}/file-lock-cleanup.XXXXXXXX")
  trap 'rm -rf -- "$build_dir"' EXIT

  mkdir -p "$build_dir/classes"
  javac -encoding UTF-8 -d "$build_dir/classes" \
    "$project_dir/FileLockTracker.java" \
    "$project_dir/BundleStreamer.java" \
    "$project_dir/TestMain.java"
  java -cp "$build_dir/classes" TestMain
else
  python3 -B -m unittest discover \
    -s "$project_dir/tests" -p 'test_*.py' -v
fi
