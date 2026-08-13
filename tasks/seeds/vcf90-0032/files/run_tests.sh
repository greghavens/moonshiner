#!/usr/bin/env bash
# Starts the loopback mock, builds and runs the client through the harness, then
# runs the protected verifier over the recorded request log.
set -uo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
BUILD=build
RUNTIME=mock/runtime
MOCK_PID=""

cleanup() {
  if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
  fi
}
trap cleanup EXIT

rm -rf "$BUILD" "$RUNTIME" out
mkdir -p "$BUILD" out

echo "== starting mock sddc-manager"
"$PYTHON" mock/vcf_mock.py &
MOCK_PID=$!

for _ in $(seq 1 100); do
  [ -s "$RUNTIME/port" ] && break
  kill -0 "$MOCK_PID" 2>/dev/null || { echo "mock exited during start-up"; exit 1; }
  sleep 0.1
done
if [ ! -s "$RUNTIME/port" ]; then
  echo "mock did not report a port within 10s"
  exit 1
fi
PORT="$(cat "$RUNTIME/port")"
BASE_URL="http://127.0.0.1:${PORT}"
echo "   listening on $BASE_URL"

echo "== compiling"
if ! javac -d "$BUILD" src/VcfDiagnostics.java test/TestMain.java; then
  echo "compilation failed"
  exit 1
fi

echo "== running harness"
java -cp "$BUILD" TestMain \
  "$BASE_URL" \
  "administrator@vsphere.local" \
  'VMw@re1!VMw@re1!' \
  "out"
HARNESS_RC=$?

cleanup
MOCK_PID=""

echo
echo "== verifying"
"$PYTHON" verify/verify.py
VERIFY_RC=$?

echo
if [ "$HARNESS_RC" -ne 0 ]; then
  echo "RESULT: FAIL (harness exited $HARNESS_RC)"
  exit 1
fi
if [ "$VERIFY_RC" -ne 0 ]; then
  echo "RESULT: FAIL (verifier exited $VERIFY_RC)"
  exit 1
fi
echo "RESULT: PASS"
