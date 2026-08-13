#!/usr/bin/env bash
# Protected verification entry point.
#
# Compiles the SDDC LCM client together with the contract-pinned harness and
# runs every scenario. No network access is required or performed: the harness
# only ever talks to its own loopback fixture.
set -euo pipefail

cd "$(dirname "$0")"

OUT=build/classes
rm -rf "$OUT"
mkdir -p "$OUT"

mapfile -t SOURCES < <(find src test -name '*.java' | sort)

javac -Xlint:all -encoding UTF-8 -d "$OUT" "${SOURCES[@]}"

exec java -ea -cp "$OUT" com.broadcom.vcf.sddclcm.harness.TestMain
