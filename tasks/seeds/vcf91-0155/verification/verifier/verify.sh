#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

sha256sum -c <<'EOF'
4e582eb981d97963223770ed4009fa2186bb38e0dd5bd2dd943931d506209e4f  go.mod
362499782093b4c10ea59496747e4e2ffc9bd747d41e6922b0c1af79057a5b9d  docs/contract.json
17cd92608e016e36a36badfad153defa46286f8daab24c7cd4fc5108812fc973  docs/official_sources.json
5a3b765685bb8cb171a7c00812a9e1216d34bff7013b47a1f242f3e88d93205b  namespacebackup/models.go
b844d7c3024b867e80c0a11bdea33a994306ec437d87b71431f63143d08e4796  namespacebackup/client_test.go
3a89196dbc71879f75074dd2c5ce93f4264786676361ab1bc492bf55c12095c6  internal/contractmock/server.go
EOF

go test -race ./...
