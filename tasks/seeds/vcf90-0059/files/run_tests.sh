#!/bin/sh
# Compile the client plus harness and run the acceptance check.
set -e
cd "$(dirname "$0")"
exec python3 verify/verify.py
