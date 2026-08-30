#!/bin/sh
set -eu

cd "$(dirname "$0")"
go test -race -count=1 ./...
