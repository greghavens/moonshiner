#!/usr/bin/env bash
# Protected verifier entry point.
#
#   1. starts the loopback mock pinned to docs/contract.json
#   2. compiles src/VcfOpsReportClient.java together with harness/TestMain.java
#   3. runs TestMain against the mock
#   4. stops the mock and asserts the recorded request wire shapes
#
# No VMware endpoint is contacted at any point; the mock binds 127.0.0.1 only.
set -u -o pipefail

cd "$(dirname "$0")"

BUILD=build
rm -rf "$BUILD"
mkdir -p "$BUILD/classes"

MOCK_PID=""
cleanup() {
  if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- 0. the client must remain a single Java source file ----------------------
java_count=$(find src -name '*.java' -type f | wc -l | tr -d ' ')
if [ "$java_count" != "1" ] || [ ! -f src/VcfOpsReportClient.java ]; then
  echo "FAIL: src/ must contain exactly one Java source file, src/VcfOpsReportClient.java (found $java_count)"
  exit 1
fi

# --- 1. mock ------------------------------------------------------------------
rm -f "$BUILD/mock_port.txt"
python3 harness/mock_server.py \
  --contract docs/contract.json \
  --log "$BUILD/requests.jsonl" \
  --port-file "$BUILD/mock_port.txt" \
  >"$BUILD/mock.out" 2>"$BUILD/mock.err" &
MOCK_PID=$!

for _ in $(seq 1 100); do
  [ -s "$BUILD/mock_port.txt" ] && break
  kill -0 "$MOCK_PID" 2>/dev/null || break
  sleep 0.1
done
if [ ! -s "$BUILD/mock_port.txt" ]; then
  echo "FAIL: the loopback mock did not start"
  sed -n '1,40p' "$BUILD/mock.err" 2>/dev/null
  exit 1
fi
PORT=$(cat "$BUILD/mock_port.txt")
BASE_URL="http://127.0.0.1:${PORT}"
echo "mock listening on ${BASE_URL}"

# --- 2. compile ---------------------------------------------------------------
if ! javac -d "$BUILD/classes" src/VcfOpsReportClient.java harness/TestMain.java 2>"$BUILD/javac.err"; then
  echo "FAIL: compilation failed"
  cat "$BUILD/javac.err"
  exit 1
fi
[ -s "$BUILD/javac.err" ] && cat "$BUILD/javac.err"

# --- 3. run -------------------------------------------------------------------
java -cp "$BUILD/classes" TestMain "$BASE_URL" "$BUILD/testmain.json" >"$BUILD/testmain.out" 2>&1
run_rc=$?
if [ "$run_rc" != "0" ]; then
  echo "note: TestMain exited $run_rc"
  sed -n '1,60p' "$BUILD/testmain.out"
fi

cleanup
MOCK_PID=""

# --- 4. assert ----------------------------------------------------------------
python3 harness/verify_wire.py
exit $?
