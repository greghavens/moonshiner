#!/usr/bin/env bash

set -u
set -o pipefail

usage() {
  cat >&2 <<'USAGE'
usage: change-executor.sh --change-dir DIR --work-dir DIR --state-dir DIR \
  --approval-file FILE [--resume]
USAGE
}

die() {
  printf 'change-executor: %s\n' "$*" >&2
  exit 2
}

CHANGE_DIR=
WORK_DIR=
STATE_DIR=
APPROVAL_FILE=
RESUME=0

while (($#)); do
  case "$1" in
    --change-dir)
      (($# >= 2)) || die '--change-dir requires a value'
      CHANGE_DIR=$2
      shift 2
      ;;
    --work-dir)
      (($# >= 2)) || die '--work-dir requires a value'
      WORK_DIR=$2
      shift 2
      ;;
    --state-dir)
      (($# >= 2)) || die '--state-dir requires a value'
      STATE_DIR=$2
      shift 2
      ;;
    --approval-file)
      (($# >= 2)) || die '--approval-file requires a value'
      APPROVAL_FILE=$2
      shift 2
      ;;
    --resume)
      RESUME=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n $CHANGE_DIR ]] || die '--change-dir is required'
[[ -n $WORK_DIR ]] || die '--work-dir is required'
[[ -n $STATE_DIR ]] || die '--state-dir is required'
[[ -n $APPROVAL_FILE ]] || die '--approval-file is required'
[[ -d $CHANGE_DIR ]] || die "change directory does not exist: $CHANGE_DIR"
[[ -d $WORK_DIR ]] || die "work directory does not exist: $WORK_DIR"
[[ -f $CHANGE_DIR/change.conf ]] || die 'change.conf is missing'

# Change bundles are local, trusted fixtures. They provide CHANGE_ID,
# EXPECTED_APPROVAL, and the ordered STEPS array.
unset CHANGE_ID EXPECTED_APPROVAL STEPS
# shellcheck source=/dev/null
source "$CHANGE_DIR/change.conf"

[[ ${CHANGE_ID-} =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die 'invalid CHANGE_ID'
[[ -n ${EXPECTED_APPROVAL-} ]] || die 'EXPECTED_APPROVAL is missing'
if ! declare -p STEPS >/dev/null 2>&1 || [[ $(declare -p STEPS) != 'declare -a'* ]]; then
  die 'STEPS must be an indexed array'
fi
((${#STEPS[@]} > 0)) || die 'STEPS must not be empty'

[[ -x $CHANGE_DIR/precheck.sh ]] || die 'precheck.sh is missing or not executable'
[[ -x $CHANGE_DIR/verify.sh ]] || die 'verify.sh is missing or not executable'

declare -A seen_steps=()
for step in "${STEPS[@]}"; do
  [[ $step =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid step name: $step"
  [[ -z ${seen_steps[$step]+x} ]] || die "duplicate step name: $step"
  seen_steps[$step]=1
  [[ -x $CHANGE_DIR/apply/$step ]] || die "apply script is missing or not executable: $step"
  [[ -x $CHANGE_DIR/rollback/$step ]] || die "rollback script is missing or not executable: $step"
done

mkdir -p "$STATE_DIR" || die "cannot create state directory: $STATE_DIR"
JOURNAL=$STATE_DIR/journal.tsv

journal() {
  local event=$1
  local detail=${2--}
  printf '%s\t%s\n' "$event" "$detail" >>"$JOURNAL" || die 'cannot write journal'
}

completed_steps=()
journal_change_id=
journal_terminal=

load_journal() {
  local event detail extra

  while IFS=$'\t' read -r event detail extra; do
    [[ -n $event ]] || continue
    case "$event" in
      BEGIN)
        if [[ -n $journal_change_id && $journal_change_id != "$detail" ]]; then
          die 'journal contains conflicting change identifiers'
        fi
        journal_change_id=$detail
        ;;
      # Successful apply operations are durable resume points.
      STEP_DONE)
        completed_steps+=("$detail")
        ;;
      COMPLETED|ROLLED_BACK)
        journal_terminal=$event
        ;;
    esac
  done <"$JOURNAL"
}

if [[ -e $JOURNAL ]]; then
  ((RESUME == 1)) || die 'journal already exists; use --resume'
  [[ -f $JOURNAL ]] || die 'journal path is not a regular file'
  load_journal
  [[ $journal_change_id == "$CHANGE_ID" ]] || die 'journal belongs to a different change'
  [[ -z $journal_terminal ]] || die "cannot resume a terminal run: $journal_terminal"
  journal RESUME "$CHANGE_ID"
else
  ((RESUME == 0)) || die 'cannot resume without an existing journal'
  : >"$JOURNAL" || die 'cannot create journal'
  journal BEGIN "$CHANGE_ID"
fi

export CHANGE_ID
export CHANGE_WORK_DIR=$WORK_DIR
export CHANGE_STATE_DIR=$STATE_DIR

journal PRECHECK_START "$CHANGE_ID"
if "$CHANGE_DIR/precheck.sh"; then
  journal PRECHECK_OK "$CHANGE_ID"
else
  rc=$?
  journal PRECHECK_FAILED "$rc"
  exit "$rc"
fi

if [[ ! -f $APPROVAL_FILE ]]; then
  journal APPROVAL_REJECTED 'missing-file'
  printf 'change-executor: approval file is missing\n' >&2
  exit 3
fi

mapfile -t approval_lines <"$APPROVAL_FILE"
if ((${#approval_lines[@]} != 1)) || [[ ${approval_lines[0]-} != "$EXPECTED_APPROVAL" ]]; then
  journal APPROVAL_REJECTED 'token-mismatch'
  printf 'change-executor: approval token rejected\n' >&2
  exit 3
fi
journal APPROVAL_OK "$CHANGE_ID"

step_is_completed() {
  local wanted=$1 completed
  for completed in "${completed_steps[@]}"; do
    [[ $completed == "$wanted" ]] && return 0
  done
  return 1
}

rollback_all() {
  local original_rc=$1
  local reason=$2
  local i step rollback_rc
  local rollback_failed=0

  journal ROLLBACK_START "$reason"
  for ((i=${#completed_steps[@]} - 1; i >= 0; i--)); do
    step=${completed_steps[$i]}
    journal ROLLBACK_STEP_START "$step"
    if "$CHANGE_DIR/rollback/$step"; then
      journal ROLLBACK_STEP_OK "$step"
    else
      rollback_rc=$?
      rollback_failed=1
      journal ROLLBACK_STEP_FAILED "$step:$rollback_rc"
    fi
  done
  if ((rollback_failed)); then
    journal ROLLED_BACK 'with-errors'
  else
    journal ROLLED_BACK 'ok'
  fi
  return "$original_rc"
}

for step in "${STEPS[@]}"; do
  if step_is_completed "$step"; then
    journal STEP_SKIPPED "$step"
    continue
  fi

  journal STEP_START "$step"
  if "$CHANGE_DIR/apply/$step"; then
    completed_steps+=("$step")
    journal STEP_OK "$step"
  else
    rc=$?
    journal STEP_FAILED "$step:$rc"
    rollback_all "$rc" "step:$step"
    exit $?
  fi
done

journal VERIFY_START "$CHANGE_ID"
if "$CHANGE_DIR/verify.sh"; then
  journal VERIFY_OK "$CHANGE_ID"
  journal COMPLETED "$CHANGE_ID"
  exit 0
else
  rc=$?
  journal VERIFY_FAILED "$rc"
  rollback_all "$rc" 'verification'
  exit $?
fi
