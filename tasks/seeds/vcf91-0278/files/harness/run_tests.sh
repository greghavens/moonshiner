#!/usr/bin/env bash
# Compile the client together with the harness and run it against the loopback
# mock. Prints the mock's request log afterwards so you can inspect the exact
# wire shape that was sent.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/.build"
LOG="$BUILD/requests.jsonl"
PORT_FILE="$BUILD/port"

rm -rf "$BUILD"
mkdir -p "$BUILD"

if [ ! -f "$ROOT/docs/contract.json" ]; then
    echo "docs/contract.json is missing; the mock is pinned to it and cannot start." >&2
    exit 1
fi

python3 "$ROOT/harness/mock_vcf_operations.py" \
    --contract "$ROOT/docs/contract.json" \
    --log "$LOG" \
    --port-file "$PORT_FILE" &
MOCK_PID=$!
cleanup() {
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
    rm -rf "$BUILD"
}
trap cleanup EXIT

for _ in $(seq 1 100); do
    [ -s "$PORT_FILE" ] && break
    sleep 0.1
done
if [ ! -s "$PORT_FILE" ]; then
    echo "the mock did not come up (check docs/contract.json)" >&2
    exit 1
fi
PORT="$(cat "$PORT_FILE")"

javac -d "$BUILD/classes" \
    "$ROOT/src/main/java/com/vmware/vcfops/VcfOperationsClient.java" \
    "$ROOT/harness/TestMain.java" || exit 1

java -cp "$BUILD/classes" com.vmware.vcfops.TestMain "http://127.0.0.1:$PORT"
STATUS=$?

echo
echo "--- mock request log ($LOG) ---"
if [ -f "$LOG" ]; then
    python3 -c '
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    e = json.loads(line)
    print("#%d %s %s%s -> %s" % (
        e["seq"], e["method"], e["path"],
        ("?" + e["rawQuery"]) if e["rawQuery"] else "", e["status"]))
    if e["body"]:
        print("    " + e["body"])
' "$LOG"
fi

exit $STATUS
