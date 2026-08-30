#!/bin/sh
set -eu

go test -race -count=1 -timeout 30s ./...
