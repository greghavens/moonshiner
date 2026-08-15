#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f MigrationPlanner.java ]]; then
  echo "MigrationPlanner.java is missing" >&2
  exit 1
fi

mapfile -t client_sources < <(find . -maxdepth 1 -type f -name '*.java' ! -name 'TestMain.java' -printf '%f\n' | LC_ALL=C sort)
if [[ ${#client_sources[@]} -ne 1 || ${client_sources[0]} != MigrationPlanner.java ]]; then
  echo "the client must be implemented only in MigrationPlanner.java" >&2
  exit 1
fi

build_dir=$(mktemp -d)
artifact_path="$build_dir/migration-plan.json"
cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT

javac -encoding UTF-8 -d "$build_dir" MigrationPlanner.java TestMain.java
java -cp "$build_dir" TestMain \
  estate-inventory.json \
  compatibility-snapshot.json \
  installer-spec.json \
  "$artifact_path"
python3 verify_plan.py \
  "$artifact_path" \
  estate-inventory.json \
  compatibility-snapshot.json \
  installer-spec.json
