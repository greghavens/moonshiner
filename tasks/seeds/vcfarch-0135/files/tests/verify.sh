#!/usr/bin/env bash
set -euo pipefail

build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT

javac -encoding UTF-8 -d "$build_dir" ArchitectureClient.java TestMain.java
java -cp "$build_dir" TestMain fixtures/estate-inventory.json > "$build_dir/architecture.json"
python3 tests/verify.py "$build_dir/architecture.json"
