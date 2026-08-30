#!/bin/sh
set -eu

exec go test -race -count=1 ./...
