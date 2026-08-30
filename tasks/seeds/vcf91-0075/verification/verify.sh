#!/bin/sh
set -eu

export GOTOOLCHAIN=local
export CCACHE_DISABLE=1

go_cache=$(mktemp -d "${TMPDIR:-/tmp}/vcf91-0075-go-cache.XXXXXX")
trap 'rm -rf "$go_cache"' EXIT HUP INT TERM
export GOCACHE="$go_cache"

go test -race -count=1 ./...
