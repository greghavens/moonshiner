#!/bin/sh
set -eu

build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM

javac --release 17 --add-modules jdk.httpserver -encoding UTF-8 -d "$build_dir" \
  VcfAutomationClient.java MockVcfAutomation.java WireVerifier.java TestMain.java
java --add-modules jdk.httpserver -cp "$build_dir" TestMain
