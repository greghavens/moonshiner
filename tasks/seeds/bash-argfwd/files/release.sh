#!/usr/bin/env bash
# release.sh — team front door for deploys: applies the standard channel,
# then hands the caller's fields through to deploy.sh.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: release.sh <deploy fields...>" >&2
  exit 64
fi

echo "release: queueing via deploy.sh"
exec bash ./deploy.sh --channel stable $*
