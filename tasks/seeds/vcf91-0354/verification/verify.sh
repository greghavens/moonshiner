#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

contract_sha="e22e0ff98ef032f56db4705aaae25990e2cb85f5588201d1e3c19053c9ad7d1d"
sources_sha="b620765c22f6b4338fdb0ee4a21bb7fe6358c789a8e838040799632d1326fa33"
test_sha="177cc81fc070c03cc683b016de2792faf25dc883b0f0012759720b7cb23231ed"

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
