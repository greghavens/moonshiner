#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v go >/dev/null 2>&1; then
  cd "$project_dir"
  env GOTOOLCHAIN=local \
    GOPROXY=off \
    GOSUMDB=off \
    GOCACHE=/tmp/go-build \
    GOPATH=/tmp/go \
    XDG_CONFIG_HOME=/tmp/go-config \
    GOTELEMETRY=off \
    GOENV=off \
    go test -race -count=1 -timeout 30s ./...
else
  python3 -B -m unittest discover \
    -s "$project_dir/tests" \
    -p 'test_*.py' \
    -v
fi
