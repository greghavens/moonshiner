#!/usr/bin/env bash
# deploy.sh — queue a deployment request for the executor.
#
# The executor replays exactly the fields recorded here, so the request spool
# stores the argument vector verbatim: a count line, then one field per line.
# (Fields are newline-free by convention; the executor rejects anything else.)
set -u

spool="request.log"

if [ "$#" -eq 0 ]; then
  echo "deploy.sh: nothing to queue" >&2
  exit 65
fi

{
  printf 'argc=%d\n' "$#"
  printf '%s\n' "$@"
} > "$spool"

printf 'queued %d field(s)\n' "$#"
