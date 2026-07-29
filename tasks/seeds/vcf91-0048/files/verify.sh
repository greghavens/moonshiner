#!/usr/bin/env bash
set -euo pipefail

build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

javac --release 17 -encoding UTF-8 -d "$build_dir" \
  SddcTrustedCertificatesClient.java TestMain.java
java -ea -cp "$build_dir" TestMain
