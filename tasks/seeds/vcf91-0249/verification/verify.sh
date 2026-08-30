#!/usr/bin/env bash
# Protected verifier. Compiles the client together with the test harness and runs the
# harness, which drives the in-process appliance and asserts the wire shape of every request.
# No socket or network access is required or performed, and no VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

rm -rf build
mkdir -p build/classes

echo "== compiling =="
javac --release 17 -nowarn -d build/classes src/SnapshotProtectionClient.java \
  harness/Json.java harness/MockSnapserviceServer.java harness/TestMain.java

echo "== running harness =="
RUNNER=(java -cp build/classes TestMain)
if command -v timeout >/dev/null 2>&1; then
  RUNNER=(timeout 120 "${RUNNER[@]}")
fi
"${RUNNER[@]}"
