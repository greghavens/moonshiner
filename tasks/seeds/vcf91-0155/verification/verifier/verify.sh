#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

sha256sum -c <<'EOF'
b99fb2272b3f074bb42d33ecd24b2b667aa843c9210af0bf48a37a3e469465c9  go.mod
362499782093b4c10ea59496747e4e2ffc9bd747d41e6922b0c1af79057a5b9d  docs/contract.json
17cd92608e016e36a36badfad153defa46286f8daab24c7cd4fc5108812fc973  docs/official_sources.json
5a3b765685bb8cb171a7c00812a9e1216d34bff7013b47a1f242f3e88d93205b  namespacebackup/models.go
ce81065a7477832b36b525fd2eda5f71f6879ad243ff67accaa45fa07a106a1d  namespacebackup/client_test.go
3a6fecc90f018fb9545d9a473cb791e792be05ef9dff29952cc39d1422e99bee  internal/contractmock/server.go
EOF

go test -race ./...
