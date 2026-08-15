#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
rm -rf .build
mkdir .build
javac -d .build MigrationPlanClient.java TestMain.java
java -cp .build TestMain
