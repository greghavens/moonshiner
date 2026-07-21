#!/usr/bin/env bash
# Protected acceptance harness for tempjanitor.sh.
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

[[ $0 == */* ]] && cd -- "${0%/*}"
ROOT=$PWD
SCRIPT=$ROOT/tempjanitor.sh
T=$ROOT/.tempjanitor-test.$$
holder=''

cleanup() {
  if [[ -n $holder ]]; then
    kill "$holder" 2>/dev/null || true
    wait "$holder" 2>/dev/null || true
  fi
  rm -rf -- "$T"
}
trap cleanup EXIT HUP INT TERM

if [[ ! -f $SCRIPT ]]; then
  printf 'FAIL: tempjanitor.sh is missing\n'
  exit 1
fi

mkdir -p "$T/root/02-expired-tree/nested" \
  "$T/root/08-open-tree/nested" "$T/bin"

young_name=$'03-young-"slash\\line\n.bin'
open_newline_name=$'07-open-newline\n'
printf 'OLD-FILE' > "$T/root/01-expired.bin"
printf 'abc' > "$T/root/02-expired-tree/a.bin"
printf '12345' > "$T/root/02-expired-tree/nested/b.bin"
printf 'YOUNG' > "$T/root/$young_name"
printf 'NOT-MINE' > "$T/root/04-wrong-owner.bin"
printf 'OTHER-FS' > "$T/root/05-cross-device.bin"
printf 'PREFIX' > "$T/root/07-open"
printf 'DIRECT' > "$T/root/07-open-file (deleted)"
printf 'TRAILING' > "$T/root/$open_newline_name"
printf 'INSIDE' > "$T/root/08-open-tree/nested/held.bin"
printf 'DO NOT TOUCH\n' > "$T/outside-precious.txt"
ln -s -- "$T/outside-precious.txt" "$T/root/02-expired-tree/escape-link"
ln -s -- "$T/outside-precious.txt" "$T/root/06-link"

# Fix every candidate mtime independently of wall-clock time, then make one
# candidate younger than the fixed cutoff (2,000,000,000 - 3,600).
find -P "$T/root" -mindepth 1 -exec touch -h -d '@1000000000' -- {} +
touch -d '@1999999000' -- "$T/root/$young_name"

SYSTEM_STAT=$(command -v stat)
export SYSTEM_STAT
export TJ_FAKE_WRONG="$T/root/04-wrong-owner.bin"
export TJ_FAKE_FOREIGN="$T/root/05-cross-device.bin"
cat > "$T/bin/stat" <<'STAT_STUB'
#!/usr/bin/env bash
set -u
last=${!#}
if [[ $# -ge 4 && $1 == -c && $2 == '%d %u %Y' ]]; then
  values=$("${SYSTEM_STAT:?}" "$@") || exit $?
  read -r device uid mtime <<< "$values"
  if [[ $last == "${TJ_FAKE_WRONG:?}" ]]; then
    uid=$((uid + 1))
  fi
  if [[ $last == "${TJ_FAKE_FOREIGN:?}" ]]; then
    device=$((device + 1))
  fi
  printf '%s %s %s\n' "$device" "$uid" "$mtime"
  exit 0
fi
exec "${SYSTEM_STAT:?}" "$@"
STAT_STUB
chmod +x "$T/bin/stat"

uid=$(id -u)
padded_uid=000$uid
lock=$T/janitor.lock
PATH_WITH_STUB=$T/bin:$PATH

checks=0
fails=0
assert_eq() {
  checks=$((checks + 1))
  if [[ $2 == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_exists() {
  checks=$((checks + 1))
  if [[ -e $2 || -L $2 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (missing: %s)\n' "$1" "$2"
}

assert_absent() {
  checks=$((checks + 1))
  if [[ ! -e $2 && ! -L $2 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s (still present: %s)\n' "$1" "$2"
}

slurp() {
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_janitor() {
  PATH=$PATH_WITH_STUB bash "$SCRIPT" "$@" > "$T/stdout" 2> "$T/stderr"
  RC=$?
  slurp OUT "$T/stdout"
  slurp ERR "$T/stderr"
}

snapshot_tree() {
  find -P "$T/root" -mindepth 1 -printf '%P|%y|%s|%T@|%l\n' | sort
}

# Hold both a direct candidate and a file below a candidate directory open.
mkfifo "$T/holder-ready" "$T/holder-release"
bash -c '
  exec 8< "$1"
  exec 9< "$2"
  exec 10< "$3"
  printf "ready\\n" > "$4"
  IFS= read -r _ < "$5"
' _ "$T/root/07-open-file (deleted)" "$T/root/08-open-tree/nested/held.bin" \
  "$T/root/$open_newline_name" "$T/holder-ready" "$T/holder-release" &
holder=$!
IFS= read -r ready < "$T/holder-ready"
assert_eq 'open-file holder handshake' ready "$ready"

# A held flock turns the janitor away before audit or mutation.
exec {guard_fd}>>"$lock"
flock -n "$guard_fd"
run_janitor --root "$T/root" --age 3600 --owner "$uid" --lock "$lock" --now 2000000000
assert_eq 'busy lock exit code' 75 "$RC"
assert_eq 'busy lock stdout is empty' '' "$OUT"
printf -v expected_busy 'tempjanitor.sh: lock busy: %s\n' "$lock"
assert_eq 'busy lock diagnostic' "$expected_busy" "$ERR"
flock -u "$guard_fd"
exec {guard_fd}>&-

before_dry=$(snapshot_tree)
run_janitor --dry-run --root "$T/root" --age 0003600 --owner "$padded_uid" --lock "$lock" --now 0002000000000
assert_eq 'dry-run exit code' 0 "$RC"
assert_eq 'dry-run stderr' '' "$ERR"
printf -v expected_dry '%s\n' \
  '{"event":"candidate","path":"01-expired.bin","action":"would_remove","reason":"eligible","bytes":8}' \
  '{"event":"candidate","path":"02-expired-tree","action":"would_remove","reason":"eligible","bytes":8}' \
  '{"event":"candidate","path":"03-young-\"slash\\line\n.bin","action":"skip","reason":"young"}' \
  '{"event":"candidate","path":"04-wrong-owner.bin","action":"skip","reason":"owner"}' \
  '{"event":"candidate","path":"05-cross-device.bin","action":"skip","reason":"filesystem"}' \
  '{"event":"candidate","path":"06-link","action":"skip","reason":"symlink"}' \
  '{"event":"candidate","path":"07-open","action":"would_remove","reason":"eligible","bytes":6}' \
  '{"event":"candidate","path":"07-open-file (deleted)","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"07-open-newline\n","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"08-open-tree","action":"skip","reason":"open"}' \
  '{"event":"summary","dry_run":true,"removed":0,"would_remove":3,"reclaimed_bytes":0,"eligible_bytes":22,"skipped":7}'
assert_eq 'dry-run byte-exact JSONL audit' "$expected_dry" "$OUT"
after_dry=$(snapshot_tree)
assert_eq 'dry-run leaves candidate tree byte-for-byte unchanged' "$before_dry" "$after_dry"

run_janitor --root "$T/root" --age 0003600 --owner "$padded_uid" --lock "$lock" --now 0002000000000
assert_eq 'live run exit code' 0 "$RC"
assert_eq 'live run stderr' '' "$ERR"
printf -v expected_live '%s\n' \
  '{"event":"candidate","path":"01-expired.bin","action":"removed","reason":"eligible","bytes":8}' \
  '{"event":"candidate","path":"02-expired-tree","action":"removed","reason":"eligible","bytes":8}' \
  '{"event":"candidate","path":"03-young-\"slash\\line\n.bin","action":"skip","reason":"young"}' \
  '{"event":"candidate","path":"04-wrong-owner.bin","action":"skip","reason":"owner"}' \
  '{"event":"candidate","path":"05-cross-device.bin","action":"skip","reason":"filesystem"}' \
  '{"event":"candidate","path":"06-link","action":"skip","reason":"symlink"}' \
  '{"event":"candidate","path":"07-open","action":"removed","reason":"eligible","bytes":6}' \
  '{"event":"candidate","path":"07-open-file (deleted)","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"07-open-newline\n","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"08-open-tree","action":"skip","reason":"open"}' \
  '{"event":"summary","dry_run":false,"removed":3,"would_remove":0,"reclaimed_bytes":22,"eligible_bytes":0,"skipped":7}'
assert_eq 'live run byte-exact JSONL and reclamation totals' "$expected_live" "$OUT"

assert_absent 'expired file reclaimed' "$T/root/01-expired.bin"
assert_absent 'expired directory reclaimed' "$T/root/02-expired-tree"
assert_exists 'young file retained' "$T/root/$young_name"
assert_exists 'wrong-owner file retained' "$T/root/04-wrong-owner.bin"
assert_exists 'foreign-filesystem candidate retained' "$T/root/05-cross-device.bin"
assert_exists 'top-level symlink retained' "$T/root/06-link"
assert_absent 'eligible prefix sibling reclaimed' "$T/root/07-open"
assert_exists 'open direct file with literal deleted suffix retained' "$T/root/07-open-file (deleted)"
assert_exists 'open direct file with trailing newline retained' "$T/root/$open_newline_name"
assert_exists 'directory with open descendant retained' "$T/root/08-open-tree/nested/held.bin"
outside=''
slurp outside "$T/outside-precious.txt"
printf -v expected_outside 'DO NOT TOUCH\n'
assert_eq 'symlink targets outside root are untouched' "$expected_outside" "$outside"

# With both open descriptors still held, an identical second live run is a
# no-op with a stable audit and stable tree.
before_second=$(snapshot_tree)
run_janitor --root "$T/root" --age 0003600 --owner "$padded_uid" --lock "$lock" --now 0002000000000
assert_eq 'idempotent run exit code' 0 "$RC"
assert_eq 'idempotent run stderr' '' "$ERR"
printf -v expected_second '%s\n' \
  '{"event":"candidate","path":"03-young-\"slash\\line\n.bin","action":"skip","reason":"young"}' \
  '{"event":"candidate","path":"04-wrong-owner.bin","action":"skip","reason":"owner"}' \
  '{"event":"candidate","path":"05-cross-device.bin","action":"skip","reason":"filesystem"}' \
  '{"event":"candidate","path":"06-link","action":"skip","reason":"symlink"}' \
  '{"event":"candidate","path":"07-open-file (deleted)","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"07-open-newline\n","action":"skip","reason":"open"}' \
  '{"event":"candidate","path":"08-open-tree","action":"skip","reason":"open"}' \
  '{"event":"summary","dry_run":false,"removed":0,"would_remove":0,"reclaimed_bytes":0,"eligible_bytes":0,"skipped":7}'
assert_eq 'idempotent run byte-exact JSONL' "$expected_second" "$OUT"
after_second=$(snapshot_tree)
assert_eq 'idempotent run leaves tree unchanged' "$before_second" "$after_second"

printf 'release\n' > "$T/holder-release"
wait "$holder"
holder=''

if ((fails > 0)); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
