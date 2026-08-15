#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT
javac -Xlint:all -Werror -d "$build_dir" ArchitectureClient.java TestMain.java
java -cp "$build_dir" TestMain
