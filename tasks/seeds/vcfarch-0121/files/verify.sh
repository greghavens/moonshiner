#!/bin/sh
set -eu

go run ./cmd/vcfplan \
  -inventory testdata/estate.json \
  -compat testdata/compatibility-snapshot.json \
  -out architecture/plan.json

# The verifier's first phase validates targetSddcSpec against the vendored
# installer OpenAPI SddcSpec. Migration and fixture checks follow only if it passes.
python3 .protected/verify.py architecture/plan.json

go test -race -timeout 30s ./...
