#!/usr/bin/env bash
# Verification gate: strict lint build, then the behavior suite. Do not edit.
set -euo pipefail
rm -rf .build
javac -Xlint:all -Werror -d .build *.java
exec java -cp .build TestMain
