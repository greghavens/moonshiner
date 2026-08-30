#!/usr/bin/env bash
# Compiles the client and the test harness, then runs the contract verification.
# Everything is loopback-only; no VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

OUT=build/classes
rm -rf "$OUT"
mkdir -p "$OUT"

echo "== compiling =="
find src test -name '*.java' -print0 | xargs -0 javac -Xlint:-this-escape -d "$OUT"

echo "== running contract verification =="
exec java -cp "$OUT" com.vmware.vcfops.networks.test.TestMain
