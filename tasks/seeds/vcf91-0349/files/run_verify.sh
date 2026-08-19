#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f VcfAutomationClient.java ]]; then
  echo "VcfAutomationClient.java is missing" >&2
  exit 1
fi

unexpected="$(find . -type f -name '*.java' \
  ! -path './VcfAutomationClient.java' \
  ! -path './MockVcfAutomationServer.java' \
  ! -path './TestMain.java' -print -quit)"
if [[ -n "$unexpected" ]]; then
  echo "The client must be implemented in one source file; found $unexpected" >&2
  exit 1
fi

vcf_build_dir="$(mktemp -d)"
trap 'rm -rf -- "$vcf_build_dir"' EXIT

javac --release 17 --add-modules jdk.httpserver \
  -classpath "$vcf_build_dir" -sourcepath '' -d "$vcf_build_dir" \
  VcfAutomationClient.java MockVcfAutomationServer.java TestMain.java
java --add-modules jdk.httpserver -cp "$vcf_build_dir" TestMain
