#!/usr/bin/env bash
set -euo pipefail

seed_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT

javac -encoding UTF-8 -d "$build_dir" \
  "$seed_root/ArchitectureClient.java" \
  "$seed_root/TestMain.java"

java -cp "$build_dir" TestMain \
  "$seed_root/fixtures/greenfield-requirements.json" \
  "$seed_root/fixtures/existing-estate.json" \
  > "$build_dir/artifact.json"

python3 "$seed_root/grading/verify.py" \
  "$build_dir/artifact.json" \
  "$seed_root/specifications/vcf-installer/vcf-installer-openapi.json" \
  "$seed_root/fixtures/greenfield-requirements.json" \
  "$seed_root/fixtures/existing-estate.json" \
  "$seed_root/grading/compatibility-snapshot.json"
