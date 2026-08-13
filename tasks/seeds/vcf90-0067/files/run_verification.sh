#!/bin/sh
# Acceptance test for vcfops_triage. Loopback only; contacts no VMware service.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

PYTHONPATH="$here/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

exec "${PYTHON:-python3}" -m unittest discover -s tests -p 'verify_*.py' -v
