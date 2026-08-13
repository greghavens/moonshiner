#!/usr/bin/env bash
# Compile everything, start the loopback mock, drive the client through it and
# print the report. No assertions -- this is the iterate-quickly loop.
# `python3 verify/verify.py` is what actually judges the result.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build
mkdir -p build/classes

echo "==> compiling"
javac -d build/classes \
    lib/MiniJson.java \
    mock/VcenterMock.java \
    harness/TestMain.java \
    src/VcenterRightSizer.java

echo "==> starting mock"
java -cp build/classes VcenterMock \
    --contract docs/contract.json \
    --config config/lab-vcenter.json \
    --fixtures mock/fixtures/inventory.json \
    --log build/requests.jsonl \
    --port-file build/mock.port &
mock_pid=$!
trap 'kill "$mock_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 200); do
    if [ -s build/mock.port ]; then
        break
    fi
    if ! kill -0 "$mock_pid" 2>/dev/null; then
        echo "mock exited before it started listening" >&2
        wait "$mock_pid" || true
        exit 1
    fi
    sleep 0.1
done

if [ ! -s build/mock.port ]; then
    echo "mock did not report a port within 20s" >&2
    exit 1
fi

port=$(cat build/mock.port)
echo "==> running TestMain against 127.0.0.1:${port}"
java -cp build/classes TestMain \
    --base-url "http://127.0.0.1:${port}" \
    --config config/lab-vcenter.json \
    --out build/report.json

echo
echo "==> request log (build/requests.jsonl)"
cat build/requests.jsonl
