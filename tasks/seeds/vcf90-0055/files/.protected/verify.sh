#!/bin/sh
# Compiles the client together with the protected harness and runs the contract
# verification against the loopback mock. No live VMware endpoint is contacted.
set -eu

cd "$(dirname "$0")/.."

rm -rf build
mkdir -p build/classes

find src .protected/src -name '*.java' -print > build/sources.txt
javac -nowarn -d build/classes @build/sources.txt

exec java -cp build/classes com.broadcom.vcf.lab.harness.TestMain "$@"
