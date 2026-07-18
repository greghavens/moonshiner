#!/usr/bin/env bash

release_error() {
  printf 'release: %s\n' "$1" >&2
  return 64
}

parse_release_args() {
  RELEASE_CHANNEL=stable
  RELEASE_LABEL=''
  RELEASE_ARTIFACTS=()

  while [ "$#" -gt 0 ]; do
    case $1 in
      --channel)
        [ "$#" -ge 2 ] || { release_error '--channel requires a value'; return $?; }
        RELEASE_CHANNEL=$2
        shift 2
        ;;
      --label)
        [ "$#" -ge 2 ] || { release_error '--label requires a value'; return $?; }
        RELEASE_LABEL=$2
        shift 2
        ;;
      --)
        shift
        RELEASE_ARTIFACTS+=("$@")
        break
        ;;
      -*)
        release_error "unknown option: $1"
        return $?
        ;;
      *)
        RELEASE_ARTIFACTS+=("$1")
        shift
        ;;
    esac
  done
}

validate_release_artifacts() {
  [ "$#" -gt 0 ] || { release_error 'at least one artifact is required'; return $?; }
  local artifact
  for artifact in "$@"; do
    [ -n "$artifact" ] || { release_error 'artifact path must not be empty'; return $?; }
    case $artifact in
      *$'\n'*) release_error 'artifact path must stay on one line'; return $? ;;
    esac
  done
}

release_main() {
  local route=$1
  shift
  parse_release_args "$@" || return $?
  validate_release_artifacts "$@" || return $?
  bash "$RELEASE_ROOT/scripts/publisher.sh" \
    "$route" "$RELEASE_CHANNEL" "$RELEASE_LABEL" "$@"
}
