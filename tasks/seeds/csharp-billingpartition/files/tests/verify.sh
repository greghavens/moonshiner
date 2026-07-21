#!/bin/sh
set -eu

sandbox_home="$PWD/.sandbox-home"
mkdir -p "$sandbox_home"

export HOME="$sandbox_home"
export DOTNET_CLI_HOME="$sandbox_home"
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
export DOTNET_NOLOGO=1
export XDG_CONFIG_HOME="$sandbox_home/.config"
export XDG_DATA_HOME="$sandbox_home/.local/share"
export NUGET_PACKAGES="$sandbox_home/.nuget/packages"

exec dotnet run \
  --project tests/BillingPartition.Tests/BillingPartition.Tests.csproj \
  --configuration Release
