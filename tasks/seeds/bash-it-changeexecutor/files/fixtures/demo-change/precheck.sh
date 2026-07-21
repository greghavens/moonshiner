#!/usr/bin/env bash

set -u

if [[ -e $CHANGE_WORK_DIR/fail-precheck ]]; then
  printf 'fixture precheck failed\n' >&2
  exit 41
fi

[[ -d $CHANGE_WORK_DIR ]] || exit 40
