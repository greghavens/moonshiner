#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

sha256sum -c <<'EOF'
383d1a3ebefd5c5d039fe8a809bba3fcb971ab7cb12289d3684606640be89a55  go.mod
7975f073c5f7a6925a629e47ec10149cfa42cf69879be406ab96b4c55eed9105  docs/contract.json
ca0ea5f1caece5a0b3e43d8bba06b798bd9ccd6b0954ed405760f13014dcf8ca  docs/official_sources.json
44807d04a92b0bdc5c7e91dda085ed31f20fa2aedda6558b4a01fce38bc0e0ed  logtask/models.go
dd2134d0580af88166e105304a111d5e3483b49a6f6b6c8ced20748b30e449da  logtask/client_test.go
aa63c7d8e3b33d53bf3aa2cbe3e90ee2d209242ebc4d5d1da4ac10115924c8e5  internal/contractmock/server.go
EOF

export GOTOOLCHAIN=local
export GOPROXY=off
export GOSUMDB=off

if [[ "$(go list -m all)" != "moonshiner/vcf91-0181" ]]; then
  echo "third-party Go modules are not allowed" >&2
  exit 1
fi

go test -race -count=1 ./...
