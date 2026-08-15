#!/usr/bin/env bash
set -euo pipefail

classes_dir=".sandbox-home/vcfarch-classes"
mkdir -p "$classes_dir"
javac --release 17 -d "$classes_dir" ArchitectureClient.java .moonshiner/TestMain.java
java -cp "$classes_dir" TestMain
