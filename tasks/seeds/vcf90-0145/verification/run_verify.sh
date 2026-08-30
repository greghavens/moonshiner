#!/usr/bin/env bash
set -euo pipefail

build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT

javac --release 17 --add-modules jdk.httpserver \
  -d "$build_dir" OperationsForNetworksClient.java TestMain.java
java --add-modules jdk.httpserver -cp "$build_dir" TestMain
