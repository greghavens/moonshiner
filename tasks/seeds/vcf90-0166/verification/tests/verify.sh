#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f src/VcfAutomationClient.java ]] ||
   [[ "$(find src -type f | LC_ALL=C sort)" != "src/VcfAutomationClient.java" ]]; then
  echo "production implementation must remain the single file src/VcfAutomationClient.java" >&2
  exit 1
fi

build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT

javac --release 17 --add-modules jdk.httpserver -d "$build_dir" \
  src/VcfAutomationClient.java \
  tests/LoopbackVcfAutomationMock.java \
  tests/TestMain.java
java --add-modules jdk.httpserver -cp "$build_dir" TestMain
