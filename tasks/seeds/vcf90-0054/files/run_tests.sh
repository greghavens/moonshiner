#!/bin/sh
set -eu
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
find . -type f -name '*.java' ! -path './tests/*' ! -path './test/*' ! -path './verify/*' ! -path './verifier/*' ! -path './harness/*' ! -path './mock/*' -print > "$tmp/sources"
test -s "$tmp/sources"
javac -d "$tmp/classes" @"$tmp/sources"
