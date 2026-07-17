#!/usr/bin/env bash
# Shop policy since the June retro: the loom tools build lint-clean or not at all.
set -eu
rm -rf out
mkdir out
javac -Xlint:all -Werror -d out *.java
exec java -cp out TestMain
