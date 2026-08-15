#!/usr/bin/env bash
set -euo pipefail

build_dir="$(mktemp -d ./.vcfarch-test-XXXXXX)"
cleanup() {
  rm -rf -- "$build_dir"
}
trap cleanup EXIT

javac -encoding UTF-8 -d "$build_dir" VcfArchitectureClient.java TestMain.java
java -cp "$build_dir" TestMain
