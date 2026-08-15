#!/usr/bin/env bash
set -euo pipefail

build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

javac --release 17 -encoding UTF-8 -d "$build_dir" MigrationPlanner.java TestMain.java
java -cp "$build_dir" TestMain \
  fixtures/estate.json \
  fixtures/compatibility-snapshot.json \
  "$build_dir/migration-plan.json"
