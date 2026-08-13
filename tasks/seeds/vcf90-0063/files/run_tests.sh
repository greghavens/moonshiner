#!/usr/bin/env bash
# Runs the protected verifier. Loopback only: each scenario starts
# .protected/mock_vcf_ops.py on an ephemeral 127.0.0.1 port. No VMware endpoint
# is contacted.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 .protected/verify.py "$@"
