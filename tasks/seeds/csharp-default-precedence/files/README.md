# MoonRelay configuration sample

This repository isolates the option-loading code used by the Relay worker. It has
no external package dependencies; `Microsoft.Extensions.Configuration` comes from
the shared ASP.NET Core framework installed with the pinned SDK.

The loader receives values after the standard providers have normalized their
keys. For example, `RELAY__MAXCONCURRENCY` is supplied to it as
`Relay:MaxConcurrency`.

Run the checks offline with:

```sh
DOTNET_CLI_HOME=/tmp/csharp-default-precedence-dotnet \
NUGET_PACKAGES=/tmp/csharp-default-precedence-nuget \
XDG_DATA_HOME=/tmp/csharp-default-precedence-xdg \
DOTNET_GENERATE_ASPNET_CERTIFICATE=false \
DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
DOTNET_CLI_TELEMETRY_OPTOUT=1 \
DOTNET_NOLOGO=1 \
dotnet run --project tests/MoonRelay.Configuration.Tests/MoonRelay.Configuration.Tests.csproj \
  --configuration Release \
  --artifacts-path /tmp/csharp-default-precedence-artifacts
```
