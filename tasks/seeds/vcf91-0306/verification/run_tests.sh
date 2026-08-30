#!/usr/bin/env bash
# Compiles the client and the harness, drives the scenarios against the loopback mock, then
# verifies the recorded request logs. No network access outside 127.0.0.1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

rm -rf build
mkdir -p build/classes

find src/main/java harness/src -name '*.java' -print > build/sources.txt
javac -nowarn -d build/classes @build/sources.txt

java -cp build/classes harness.TestMain "$ROOT"
java -cp build/classes harness.Verifier "$ROOT"
