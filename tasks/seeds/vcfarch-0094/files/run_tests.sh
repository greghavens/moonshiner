#!/usr/bin/env bash
set -euo pipefail

build_dir=".build"
mkdir -p "$build_dir"
javac --release 17 -d "$build_dir" VcfArchitectureClient.java .protected/TestMain.java
java -cp "$build_dir" TestMain
