#!/usr/bin/env bash
set -euo pipefail

export DOTNET_CLI_HOME="${TMPDIR:-/tmp}/moonshiner-dotnet-home"
export HOME="$DOTNET_CLI_HOME"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
export DOTNET_NOLOGO=1

dotnet run \
  --project tests/MobileOfflineOutbox.ProtectedTests/MobileOfflineOutbox.ProtectedTests.csproj \
  --configuration Release
