#!/usr/bin/env bash
# Runs the SDDC LCM rollout contract test.  Loopback only; no VMware endpoint
# and no network access are involved.
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
"$PY" - <<'EOF'
import sys
if sys.version_info < (3, 10):
    sys.exit("python 3.10 or newer is required, found %s" % sys.version.split()[0])
EOF

# The fixture, the mock and the contract test are fixed inputs.  Refuse to grade
# a run in which they were edited.
expected_hashes() {
  cat <<'EOF'
0a849c83c326ba6d98b77841ec502fa564a886349871154912514237be0734a6  fixtures/rollout-plan.json
26217a411975b510a76f458e119072c4a0da5272c65e1f767d83d321837443c5  tools/mock_sddc_lcm.py
ad65cbad97a8fd2410e918733c9010e14405ae9d346476d89df6479d88fdafe7  tests/test_rollout_contract.py
EOF
}

if command -v sha256sum >/dev/null 2>&1; then
  if ! expected_hashes | sha256sum --check --status; then
    echo "protected files were modified:" >&2
    expected_hashes | sha256sum --check 2>&1 | grep -v ': OK$' >&2 || true
    exit 2
  fi
else
  echo "sha256sum is unavailable; skipping the integrity check" >&2
fi

exec "$PY" -m unittest -v tests.test_rollout_contract
