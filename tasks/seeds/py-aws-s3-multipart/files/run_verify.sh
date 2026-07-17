#!/usr/bin/env bash
# CI entrypoint — protected file. The uploader test suite runs from the
# project's own virtualenv; the pinned boto3 environment is part of the
# definition of done.
if [ ! -x .venv/bin/python ]; then
  echo "FAIL: .venv/bin/python not found — create the project virtualenv and install requirements.txt into it" >&2
  exit 1
fi
exec .venv/bin/python -m pytest -q test_uploader.py
