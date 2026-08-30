#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="$script_dir/.verify-build"
trap 'rm -rf -- "$build_dir"' EXIT
rm -rf -- "$build_dir"
mkdir -p "$build_dir"

cd "$script_dir"
javac -d "$build_dir" AutomationClient.java TestMain.java
java -cp "$build_dir" TestMain
