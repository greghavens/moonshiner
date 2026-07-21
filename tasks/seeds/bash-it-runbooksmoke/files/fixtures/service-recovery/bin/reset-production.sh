#!/usr/bin/env bash

set -u

printf 'destructive example ran\n' >&2
: > "${RUNBOOK_DESTRUCTIVE_SENTINEL:?}"
rm -rf -- production-data
printf 'production data reset\n'
