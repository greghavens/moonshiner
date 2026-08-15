#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
verify_dir=$(mktemp -d)
trap 'rm -rf -- "$verify_dir"' EXIT

mkdir -p "$verify_dir/classes" "$verify_dir/output"
javac -encoding UTF-8 -d "$verify_dir/classes" \
  "$repo_dir/ArchitectureClient.java" \
  "$repo_dir/tests/TestMain.java"

java -cp "$verify_dir/classes" TestMain \
  "$repo_dir/fixtures/estate-inventory.json" \
  "$repo_dir/fixtures/compatibility-snapshot.json" \
  "$verify_dir/output"

python3 "$repo_dir/tests/verify.py" \
  "$verify_dir/output" \
  "$repo_dir/fixtures/estate-inventory.json" \
  "$repo_dir/fixtures/compatibility-snapshot.json" \
  "$repo_dir/fixtures/migration-plan.schema.json" \
  "$repo_dir/specifications/vcf-installer/vcf-installer-openapi.json" \
  "$repo_dir/research-consulted.md"
