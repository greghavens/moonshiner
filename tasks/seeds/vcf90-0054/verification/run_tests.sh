#!/bin/sh
# Compiles the client together with the test harness and runs the wire-shape scenarios
# against the loopback mock. No network access, no external dependencies, JDK only.
set -eu

cd "$(dirname "$0")"

rm -rf build/classes build/request-logs
mkdir -p build/classes

find src harness -name '*.java' -print > build/sources.txt
javac -nowarn -d build/classes @build/sources.txt

exec java -cp build/classes com.example.vcf.harness.TestMain
