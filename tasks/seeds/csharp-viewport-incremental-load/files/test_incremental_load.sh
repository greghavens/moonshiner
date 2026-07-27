#!/usr/bin/env bash
set -euo pipefail

task_tmp="$(mktemp -d "${TMPDIR:-/tmp}/csharp-viewport-incremental-load.XXXXXX")"
trap 'rm -rf -- "$task_tmp"' EXIT

export DOTNET_CLI_HOME="$task_tmp/dotnet-home"
export NUGET_PACKAGES="$task_tmp/nuget"
export XDG_DATA_HOME="$task_tmp/xdg"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_GENERATE_ASPNET_CERTIFICATE=false
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
export DOTNET_NOLOGO=1

dotnet run \
  --project tests/ViewportIncrementalLoad.ProtectedTests/ViewportIncrementalLoad.ProtectedTests.csproj \
  --configuration Release \
  --artifacts-path "$task_tmp/artifacts" \
  --nologo
