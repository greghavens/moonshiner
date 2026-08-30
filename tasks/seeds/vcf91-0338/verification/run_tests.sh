#!/usr/bin/env bash
# Compile the client, run it against the loopback mock, then verify the traffic.
#
# PROTECTED FILE - part of the graded harness, do not modify.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/.run"

PYTHON="${PYTHON:-python3}"
JAVAC="${JAVAC:-javac}"
JAVA="${JAVA:-java}"

export VCFA_MOCK_TOKEN="eyJhbGciOiJSUzI1NiJ9.vcfa91-triage-fixture-token"
export VCFA_MOCK_STATE="$RUN"
export VCFA_RUN_DIR="$RUN"

rm -rf "$RUN"
mkdir -p "$RUN"

MOCK_PID=""
cleanup() {
  if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
  fi
}
trap cleanup EXIT

DEPLOYMENT_ID="$("$PYTHON" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["ids"]["deploymentId"])' \
  "$ROOT/mock/fixtures.json")"

echo "== starting loopback mock =="
"$PYTHON" "$ROOT/mock/vcfa_mock.py" >"$RUN/mock.out" 2>"$RUN/mock.err" &
MOCK_PID=$!

for _ in $(seq 1 150); do
  [ -f "$RUN/endpoint.json" ] && break
  kill -0 "$MOCK_PID" 2>/dev/null || break
  sleep 0.1
done

if [ ! -f "$RUN/endpoint.json" ]; then
  echo "mock failed to start:"
  cat "$RUN/mock.err" 2>/dev/null
  exit 1
fi

BASE_URL="$("$PYTHON" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["baseUrl"])' \
  "$RUN/endpoint.json")"
echo "mock ready at $BASE_URL"

echo
echo "== compiling =="
if ! "$JAVAC" -d "$RUN/classes" \
      "$ROOT/src/VcfaTriage.java" \
      "$ROOT/harness/TestMain.java" \
      "$ROOT/harness/Json.java"; then
  echo "compilation failed"
  exit 1
fi

echo
echo "== running client =="
"$JAVA" -cp "$RUN/classes" TestMain \
  "$BASE_URL" "$VCFA_MOCK_TOKEN" "$DEPLOYMENT_ID" "$RUN/report.txt"
CLIENT_STATUS=$?
echo "client exited with $CLIENT_STATUS"

cleanup
MOCK_PID=""

echo
echo "== verifying =="
"$PYTHON" "$ROOT/verify/verify.py"
exit $?
