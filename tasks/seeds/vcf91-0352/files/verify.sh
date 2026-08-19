#!/usr/bin/env bash
set -euo pipefail

contract_sha="5d6919b8fdf5696b8ee5b7d8323f08be54222150bce2655dc90402f5a7c656ba"
sources_sha="c02a04d293344a778b210ff873ceb6d8b5ca9518eab05fcbb32ae8e6beb4fdee"
test_sha="6a4f668a9b5c14d0ccb29fa23980e1dd19ffd027c408d5ae0c4ff612a41c6ade"

[[ "$(sha256sum docs/contract.json | awk '{print $1}')" == "$contract_sha" ]] || {
  echo "protected contract fixture changed" >&2
  exit 1
}
[[ "$(sha256sum docs/official_sources.json | awk '{print $1}')" == "$sources_sha" ]] || {
  echo "protected source ledger changed" >&2
  exit 1
}
[[ "$(sha256sum TestMain.java | awk '{print $1}')" == "$test_sha" ]] || {
  echo "protected TestMain harness changed" >&2
  exit 1
}

build_dir="$(mktemp -d)"
trap 'rm -rf -- "$build_dir"' EXIT
javac --add-modules jdk.httpserver -encoding UTF-8 -d "$build_dir" \
  src/AutomationClient.java TestMain.java
java --add-modules jdk.httpserver -cp "$build_dir" TestMain
