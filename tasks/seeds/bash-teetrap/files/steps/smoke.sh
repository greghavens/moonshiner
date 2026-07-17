#!/usr/bin/env bash
# smoke step: sanity-check the artifacts the earlier steps produced
set -u

if [ ! -f out/bundle.txt ]; then
  echo 'smoke: error: out/bundle.txt is missing' >&2
  exit 5
fi
if ! grep -q '^== bundle ==$' out/bundle.txt; then
  echo 'smoke: error: bundle header missing' >&2
  exit 5
fi
echo 'smoke: bundle looks good'

if [ ! -f out/schema.log ]; then
  echo 'smoke: error: out/schema.log is missing' >&2
  exit 5
fi
echo 'smoke: schema log present'
