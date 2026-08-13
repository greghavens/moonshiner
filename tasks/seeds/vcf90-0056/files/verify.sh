#!/bin/sh
# Compiles the client together with the test harness and runs it against the loopback mock.
# Exits 0 only when every contract assertion holds.
set -eu

cd "$(dirname "$0")"

MANIFEST=harness/protected.sha256
if [ ! -f "$MANIFEST" ]; then
    echo "verify: missing $MANIFEST" >&2
    exit 1
fi
if ! sha256sum -c "$MANIFEST" >/dev/null 2>&1; then
    echo "verify: the contract, the fixtures or the test harness have been modified." >&2
    echo "verify: these files are not part of the exercise - restore them and re-run." >&2
    sha256sum -c "$MANIFEST" 2>/dev/null | grep -v ': OK$' >&2 || true
    exit 1
fi

rm -rf build
mkdir -p build/classes
find src harness/src -name '*.java' -print > build/sources.txt
javac -d build/classes @build/sources.txt

exec java -cp build/classes com.example.vcf.harness.TestMain
