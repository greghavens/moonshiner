#!/bin/sh
# Protected verifier entry point.
#
# Runs the table-driven verifier in ./verify under the race detector. The
# verifier only ever contacts a loopback appliance it starts itself.
set -eu

cd "$(dirname "$0")/.."

GOFLAGS=-mod=mod
GOPROXY=off
GOTOOLCHAIN=local
CGO_ENABLED=1
export GOFLAGS GOPROXY GOTOOLCHAIN CGO_ENABLED

go build ./...
exec go test -race -count=1 ./verify/
