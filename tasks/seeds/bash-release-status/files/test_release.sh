#!/usr/bin/env bash
# Hermetic regression harness for release.sh.
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

[[ $0 == */* ]] && cd -- "${0%/*}"
ROOT=$PWD
T=$ROOT/_t
rm -rf -- "$T" "$ROOT/dist"
mkdir -p "$T/fakebin" "$T/src-ok" "$T/src-bad"
cleanup() { rm -rf -- "$T" "$ROOT/dist"; }
trap cleanup EXIT

printf 'alpha component\n' > "$T/src-ok/a.txt"
printf 'bravo component\n' > "$T/src-ok/b.txt"
cp "$T/src-ok/a.txt" "$T/src-bad/a.txt"
: > "$T/src-bad/b.txt"

cat > "$T/fakebin/git" <<'FAKE_GIT'
#!/usr/bin/env bash
set -u
: "${GIT_CALLS:?GIT_CALLS must be set}"
printf 'arg=%s\n' "$@" >> "$GIT_CALLS"
FAKE_GIT
chmod +x "$T/fakebin/git"

checks=0
fails=0
RC=0
OUT=''
ERR=''

slurp() {
  printf -v "$1" ''
  [[ -f $2 ]] || return 0
  IFS= read -r -d '' "$1" < "$2" || true
}

run_release() {
  : > "$T/out"
  : > "$T/err"
  env PATH="$T/fakebin:$PATH" GIT_CALLS="$T/git.calls" "$@" \
    > "$T/out" 2> "$T/err"
  RC=$?
  slurp OUT "$T/out"
  slurp ERR "$T/err"
}

assert_eq() {
  checks=$((checks + 1))
  if [[ $2 == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_absent() {
  checks=$((checks + 1))
  if [[ ! -e $2 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s: unexpectedly exists: %s\n' "$1" "$2"
}

expect() {
  assert_eq "$1 exit" "$2" "$RC"
  assert_eq "$1 stdout" "$3" "$OUT"
  assert_eq "$1 stderr" "$4" "$ERR"
}

if [[ ! -f release.sh || ! -f scripts/build-dist.sh ]]; then
  printf 'FAIL release.sh and scripts/build-dist.sh must exist at the workspace root\n'
  exit 1
fi

# Dry-run prints the complete plan even without a source tree and has no effects.
rm -f "$T/git.calls"
printf -v expected_dry '%s\n' \
  'release: dry-run: build moonshiner-2.4.1.tar' \
  'release: dry-run: checksum moonshiner-2.4.1.tar.sha256' \
  'release: dry-run: tag v2.4.1'
run_release bash "$ROOT/release.sh" --dry-run 2.4.1
expect 'dry-run plan' 0 "$expected_dry" ''
assert_absent 'dry-run does not create dist' "$ROOT/dist"
assert_absent 'dry-run does not invoke git' "$T/git.calls"

# A successful build retains exact artifact/checksum bytes and creates one tag.
rm -rf "$ROOT/dist"
rm -f "$T/git.calls"
printf -v expected_ok '%s\n' \
  'release: building moonshiner-2.4.1.tar' \
  'build: packaging a.txt' \
  'build: packaging b.txt' \
  'build: wrote dist/moonshiner-2.4.1.tar' \
  'release: checksum moonshiner-2.4.1.tar.sha256' \
  'release: tagged v2.4.1'
run_release env RELEASE_SOURCE_DIR="$T/src-ok" bash "$ROOT/release.sh" 2.4.1
expect 'successful release' 0 "$expected_ok" ''

printf -v expected_artifact 'package=2.4.1\nfile=a.txt\nalpha component\nfile=b.txt\nbravo component\n'
artifact=''
slurp artifact "$ROOT/dist/moonshiner-2.4.1.tar"
assert_eq 'successful artifact bytes' "$expected_artifact" "$artifact"

expected_hash=$(printf '%s' "$expected_artifact" | sha256sum)
expected_hash=${expected_hash%% *}
printf -v expected_checksum '%s  %s\n' "$expected_hash" 'moonshiner-2.4.1.tar'
checksum=''
slurp checksum "$ROOT/dist/moonshiner-2.4.1.tar.sha256"
assert_eq 'successful SHA-256 basename record' "$expected_checksum" "$checksum"

printf -v expected_build_log '%s\n' \
  'build: packaging a.txt' \
  'build: packaging b.txt' \
  'build: wrote dist/moonshiner-2.4.1.tar'
build_log=''
slurp build_log "$ROOT/dist/build.log"
assert_eq 'successful build output is mirrored' "$expected_build_log" "$build_log"

printf -v expected_git '%s\n' \
  'arg=tag' \
  'arg=-a' \
  'arg=v2.4.1' \
  'arg=-m' \
  'arg=Release 2.4.1'
git_calls=''
slurp git_calls "$T/git.calls"
assert_eq 'successful release creates exact annotated tag' "$expected_git" "$git_calls"

# The second source is empty. The builder writes a partial archive, then exits 23.
rm -rf "$ROOT/dist"
rm -f "$T/git.calls"
printf -v expected_bad_out '%s\n' \
  'release: building moonshiner-2.4.1.tar' \
  'build: packaging a.txt' \
  'build: packaging b.txt' \
  "build: error: empty source file: $T/src-bad/b.txt"
printf -v expected_bad_err '%s\n' \
  'release: build failed (exit 23); release not tagged'
run_release env RELEASE_SOURCE_DIR="$T/src-bad" bash "$ROOT/release.sh" 2.4.1
expect 'failed build aborts release' 23 "$expected_bad_out" "$expected_bad_err"

printf -v expected_bad_log '%s\n' \
  'build: packaging a.txt' \
  'build: packaging b.txt' \
  "build: error: empty source file: $T/src-bad/b.txt"
bad_log=''
slurp bad_log "$ROOT/dist/build.log"
assert_eq 'failed build output remains mirrored' "$expected_bad_log" "$bad_log"
assert_absent 'partial artifact is cleaned' "$ROOT/dist/moonshiner-2.4.1.tar"
assert_absent 'failed build has no checksum' "$ROOT/dist/moonshiner-2.4.1.tar.sha256"
assert_absent 'failed build never invokes git' "$T/git.calls"

if [[ $fails -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all %d checks passed\n' "$checks"
exit 0
