#!/bin/sh
# Build, exercise the client against the loopback mock, then check the result and the wire shape.
# Contacts nothing but 127.0.0.1.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

sh ./run.sh
exec python3 verify/verify.py "$ROOT"
