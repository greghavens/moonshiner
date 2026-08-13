#!/usr/bin/env bash
set -euo pipefail

sha256sum -c .moonshiner/protected.sha256
go test -race ./...
