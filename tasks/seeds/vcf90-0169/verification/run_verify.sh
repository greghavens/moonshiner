#!/usr/bin/env bash
set -euo pipefail

shopt -s nullglob
production_sources=()
while IFS= read -r -d '' source; do
  source="${source#./}"
  case "$source" in
    TestMain.java|ContractMockServer.java) ;;
    *) production_sources+=("$source") ;;
  esac
done < <(find . -type f -name '*.java' -print0)
if [[ "${production_sources[*]-}" != "AutomationClient.java" ]]; then
  echo "expected exactly one production source: AutomationClient.java" >&2
  exit 1
fi

build_dir="$(mktemp -d .verify-build.XXXXXX)"
trap 'rm -rf "$build_dir"' EXIT

javac --add-modules jdk.httpserver -classpath "$build_dir" \
  -encoding UTF-8 -d "$build_dir" \
  AutomationClient.java ContractMockServer.java TestMain.java
java --add-modules jdk.httpserver -cp "$build_dir" TestMain
