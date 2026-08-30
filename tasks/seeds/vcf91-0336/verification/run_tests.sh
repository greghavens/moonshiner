#!/bin/sh
# Compiles the client and the harness, then runs the verification suite.
# Everything runs against a loopback mock; no VMware endpoint is contacted.
set -eu

cd "$(dirname "$0")"

rm -rf build
mkdir -p build

find src test -name '*.java' -print > build/sources.txt
javac -d build @build/sources.txt

exec java -cp build TestMain "$PWD"
