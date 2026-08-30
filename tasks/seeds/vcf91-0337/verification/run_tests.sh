#!/usr/bin/env bash
# Compiles the client together with the harness and runs the contract conformance suite.
# Everything happens on 127.0.0.1; no VMware endpoint is contacted.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v javac >/dev/null 2>&1; then
    echo "javac not found on PATH; a JDK 17 or newer is required" >&2
    exit 127
fi

rm -rf build/classes
mkdir -p build/classes

javac --release 17 -Xlint:-options -d build/classes harness/*.java src/*.java

exec java -cp build/classes TestMain
