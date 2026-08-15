#!/usr/bin/env bash
set -euo pipefail

build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT

javac --release 17 -d "$build_dir" MigrationPlanClient.java TestMain.java
java -cp "$build_dir" TestMain
