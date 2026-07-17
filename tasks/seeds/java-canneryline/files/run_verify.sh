#!/usr/bin/env bash
# Build the whole tree the way CI does, then run the checks.
set -eu
rm -rf out
mkdir out
javac -d out $(find . -name '*.java' -not -path './out/*' | sort)
exec java -cp out TestMain
