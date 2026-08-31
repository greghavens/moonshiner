#!/bin/sh
# Runs the task's checks.
# Artifacts land in out/ for the verifier to read.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

rm -rf build out
mkdir -p build out

javac -d build harness/MiniJson.java harness/MockVcfOnServer.java harness/TestMain.java \
    src/VcfOnAppInventory.java

java -cp build TestMain out
