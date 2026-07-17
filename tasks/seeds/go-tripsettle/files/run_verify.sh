#!/usr/bin/env bash
# CI gate: formatting, vet, then tests. All three must be clean.
set -u

unformatted=$(gofmt -l .)
if [ -n "$unformatted" ]; then
	echo "gofmt: the following files need formatting:"
	echo "$unformatted"
	exit 1
fi

go vet ./... || exit 1

exec go test -race -timeout 60s ./...
