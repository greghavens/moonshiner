#!/usr/bin/env bash

set -u

if [[ -e $CHANGE_WORK_DIR/fail-verify ]]; then
  printf 'fixture verification failed\n' >&2
  exit 55
fi

[[ $(<"$CHANGE_WORK_DIR/alpha") == alpha ]] || exit 51
[[ $(<"$CHANGE_WORK_DIR/beta") == beta ]] || exit 52
[[ $(<"$CHANGE_WORK_DIR/gamma") == gamma ]] || exit 53
[[ $(<"$CHANGE_WORK_DIR/delta") == delta ]] || exit 54

expected=$'10-write-alpha\n20-write-beta\n30-write-gamma\n40-write-delta'
[[ $(<"$CHANGE_WORK_DIR/mutations.log") == "$expected" ]] || exit 56
