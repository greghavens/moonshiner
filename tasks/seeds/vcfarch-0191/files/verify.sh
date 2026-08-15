#!/usr/bin/env bash
set -euo pipefail

mkdir -p .sandbox-home/verify-classes
javac -encoding UTF-8 -d .sandbox-home/verify-classes MigrationPlanClient.java TestMain.java
java -cp .sandbox-home/verify-classes TestMain
