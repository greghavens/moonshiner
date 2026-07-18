#!/usr/bin/env bash
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RELEASE_ROOT=$(CDPATH= cd -- "$script_dir/.." && pwd)
export RELEASE_ROOT

# shellcheck source=scripts/release-lib.sh
. "$RELEASE_ROOT/scripts/release-lib.sh"

if [ -z "${RELEASE_CHANNEL:-}" ]; then
  printf 'ci-release: RELEASE_CHANNEL is required\n' >&2
  exit 64
fi

ci_args=(--channel "$RELEASE_CHANNEL")
if [ -n "${RELEASE_LABEL:-}" ]; then
  ci_args+=(--label "$RELEASE_LABEL")
fi
ci_args+=(--)
if [ -n "${RELEASE_ARTIFACT_ONE:-}" ]; then
  ci_args+=("$RELEASE_ARTIFACT_ONE")
fi
if [ -n "${RELEASE_ARTIFACT_TWO:-}" ]; then
  ci_args+=("$RELEASE_ARTIFACT_TWO")
fi

release_main ci "${ci_args[@]}"
