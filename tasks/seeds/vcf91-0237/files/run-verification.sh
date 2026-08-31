#!/usr/bin/env bash
# Runs the task's checks.
#
# Compiles the SDDC LCM client together with the focused harness and
# runs every scenario. No network access is required or performed: the harness
# Runs the task's checks.
set -euo pipefail

cd "$(dirname "$0")"

OUT=build/classes
rm -rf "$OUT"
mkdir -p "$OUT"

mapfile -t SOURCES < <(find src test -name '*.java' | sort)

javac -Xlint:all -encoding UTF-8 -d "$OUT" "${SOURCES[@]}"

exec java -ea -cp "$OUT" com.broadcom.vcf.sddclcm.harness.TestMain
