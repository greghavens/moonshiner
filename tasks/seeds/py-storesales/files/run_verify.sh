#!/usr/bin/env bash
# CI entrypoint — protected file. The suite runs from the project's pinned
# conda env at ./env; the environment is part of the definition of done.
if [ ! -x ./env/bin/python ]; then
  echo "FAIL: ./env/bin/python not found — create the pinned local env at ./env from environment.yml" >&2
  exit 1
fi
exec ./env/bin/python -m pytest -q test_salesreport.py
