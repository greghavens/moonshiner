#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "$0")/.." && pwd)
verify_tmp=$(mktemp -d)
trap 'rm -rf "$verify_tmp"' EXIT

javac -encoding UTF-8 -d "$verify_tmp/classes" \
  "$project_dir/ArchitectureClient.java" \
  "$project_dir/TestMain.java"

java -cp "$verify_tmp/classes" TestMain \
  "$project_dir/fixtures/estate-inventory.json" \
  "$project_dir/fixtures/compatibility-snapshot.json" \
  "$verify_tmp/architecture.json"

python3 "$project_dir/.moonshiner/verify.py" \
  "$verify_tmp/architecture.json" \
  "$project_dir"
