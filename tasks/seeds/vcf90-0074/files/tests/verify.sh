#!/usr/bin/env bash
set -euo pipefail

sha256sum -c tests/protected.sha256
go test -race ./...
