#!/usr/bin/env bash
# CI entrypoint — protected file. The suite runs from the project's own
# environment at ./env; the environment is part of the definition of done.
if [ ! -x ./env/bin/python ]; then
  echo "FAIL: ./env/bin/python not found — set up the project environment at ./env from the manifest committed in this repo" >&2
  exit 1
fi
exec ./env/bin/python -m pytest -q test_panelcalc.py
