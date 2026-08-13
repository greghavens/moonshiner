#!/usr/bin/env bash
# Compiles the client, starts the loopback SDDC LCM fixture on an ephemeral port,
# runs the rollout through TestMain, and stops the fixture.
#
# Artifacts land in build/:
#   build/requests.jsonl  every request the fixture received
#   build/report.json     the rollout report the client returned
#   build/mock.log        fixture stdout/stderr
set -uo pipefail

cd "$(dirname "$0")"

TOKEN="${LCM_TOKEN:-eyJhbGciOiJIUzI1NiJ9.fixture-lcm-token.6Qw1nB2xKcVpTdLaRfYs}"
BUILD=build

rm -rf "$BUILD"
mkdir -p "$BUILD"

echo "==> compiling fixture"
javac -d "$BUILD/mock-classes" mock/SddcLcmMock.java harness/Json.java || exit 1

echo "==> compiling client + harness"
javac -Xlint:-options -d "$BUILD/classes" harness/Json.java harness/TestMain.java src/VcfLcmClient.java || exit 1

echo "==> starting fixture"
rm -f "$BUILD/port"
java -cp "$BUILD/mock-classes" SddcLcmMock \
    --port 0 \
    --log "$BUILD/requests.jsonl" \
    --portfile "$BUILD/port" \
    --token "$TOKEN" > "$BUILD/mock.log" 2>&1 &
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null' EXIT

for _ in $(seq 1 100); do
    [ -s "$BUILD/port" ] && break
    sleep 0.1
done
if [ ! -s "$BUILD/port" ]; then
    echo "fixture did not start:" >&2
    cat "$BUILD/mock.log" >&2
    exit 1
fi
PORT="$(cat "$BUILD/port")"
echo "==> fixture on 127.0.0.1:$PORT"

echo "==> running rollout"
java -cp "$BUILD/classes" TestMain \
    "http://127.0.0.1:$PORT/sddc-lcm" \
    "$TOKEN" \
    fixtures/upgrade-request.json \
    "$BUILD/report.json"
STATUS=$?

kill "$MOCK_PID" 2>/dev/null
wait "$MOCK_PID" 2>/dev/null

echo "==> requests recorded: $(wc -l < "$BUILD/requests.jsonl")"
exit "$STATUS"
