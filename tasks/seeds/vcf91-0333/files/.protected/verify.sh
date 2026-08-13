#!/usr/bin/env bash
# Protected verification entry point.
#
# Compiles the supplied harness together with the candidate client and runs TestMain from the task
# root. The harness starts a contract-pinned mock on an ephemeral 127.0.0.1 port; no live VMware
# endpoint is contacted.
#
# Do not edit.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

if ! command -v javac >/dev/null 2>&1 || ! command -v java >/dev/null 2>&1; then
  echo "FATAL: a JDK providing javac and java is required." >&2
  exit 2
fi

OUT="$ROOT/build/classes"
rm -rf "$ROOT/build"
cleanup() {
  rm -rf "$ROOT/build"
}
trap cleanup EXIT
mkdir -p "$OUT"

echo "== compiling =="
if ! javac -encoding UTF-8 -d "$OUT" \
    "$ROOT/src/Json.java" \
    "$ROOT/src/VcfCatalogClient.java" \
    "$ROOT/.protected/MockVcfAutomation.java" \
    "$ROOT/.protected/TestMain.java"; then
  echo "FAIL - compilation failed" >&2
  exit 1
fi

echo "== verifying =="
java -cp "$OUT" TestMain
