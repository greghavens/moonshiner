#!/usr/bin/env bash
set -eu

if [ -z "${RELEASE_ROOT:-}" ]; then
  script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  RELEASE_ROOT=$(CDPATH= cd -- "$script_dir/.." && pwd)
  export RELEASE_ROOT
fi

# shellcheck source=scripts/release-lib.sh
. "$RELEASE_ROOT/scripts/release-lib.sh"

release_main "${RELEASE_ROUTE:-direct}" "$@"
