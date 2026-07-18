#!/usr/bin/env bash
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

[[ $0 == */* ]] && cd -- "${0%/*}"
ROOT=$PWD
T=$ROOT/_t
rm -rf "$T"
mkdir -p "$T"
cleanup() { rm -rf "$T"; }
trap cleanup EXIT

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

run_cmd() {
  "$@" > "$T/out" 2> "$T/err"
  RC=$?
  slurp OUT "$T/out"
  slurp ERR "$T/err"
}

assert_eq() {
  checks=$((checks + 1))
  if [[ $2 == "$3" ]]; then return 0; fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

expect() {
  assert_eq "$1 exit" "$2" "$RC"
  assert_eq "$1 stdout" "$3" "$OUT"
  assert_eq "$1 stderr" "$4" "$ERR"
}

expected_direct=$'route=direct\nchannel=beta\nlabel=July RC\nartifact-count=2\nartifact=dist/app bundle.tgz\nartifact=dist/check sums.txt\n'
expected_container=$'route=container\nchannel=beta\nlabel=July RC\nartifact-count=2\nartifact=dist/app bundle.tgz\nartifact=dist/check sums.txt\n'
expected_ci=$'route=ci\nchannel=rc\nlabel=CI build 44\nartifact-count=2\nartifact=dist/app.tgz\nartifact=dist/check sums.txt\n'

run_cmd bash "$ROOT/bin/release" --channel beta --label 'July RC' -- 'dist/app bundle.tgz' 'dist/check sums.txt'
expect 'bin direct route' 0 "$expected_direct" ''

run_cmd bash "$ROOT/scripts/release.sh" --channel beta --label 'July RC' -- 'dist/app bundle.tgz' 'dist/check sums.txt'
expect 'script direct route' 0 "$expected_direct" ''

run_cmd bash "$ROOT/container/entrypoint.sh" release --channel beta --label 'July RC' -- 'dist/app bundle.tgz' 'dist/check sums.txt'
expect 'container release route' 0 "$expected_container" ''

run_cmd env RELEASE_CHANNEL=rc RELEASE_LABEL='CI build 44' RELEASE_ARTIFACT_ONE='dist/app.tgz' RELEASE_ARTIFACT_TWO='dist/check sums.txt' make --no-print-directory -C "$ROOT" release
expect 'Make CI route' 0 "$expected_ci" ''

# The shared helper is also sourced by internal release automation.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -eu' \
  'RELEASE_ROOT=$1' \
  'export RELEASE_ROOT' \
  '. "$RELEASE_ROOT/scripts/release-lib.sh"' \
  'shift' \
  'release_main sourced "$@"' > "$T/source-driver.sh"
expected_sourced=$'route=sourced\nchannel=nightly\nlabel=-\nartifact-count=2\nartifact=one file.tar\nartifact=--symbols.tgz\n'
run_cmd bash "$T/source-driver.sh" "$ROOT" --channel nightly -- 'one file.tar' '--symbols.tgz'
expect 'sourced helper route' 0 "$expected_sourced" ''

expected_default=$'route=direct\nchannel=stable\nlabel=-\nartifact-count=2\nartifact=dist/app.tgz\nartifact=dist/checksums.txt\n'
run_cmd bash "$ROOT/bin/release" dist/app.tgz dist/checksums.txt
expect 'positional default channel' 0 "$expected_default" ''

run_cmd bash "$ROOT/bin/release" --channel stable
expect 'option-only release rejected' 64 '' $'release: at least one artifact is required\n'

run_cmd bash "$ROOT/bin/release" --channel
expect 'missing option value' 64 '' $'release: --channel requires a value\n'

run_cmd bash "$ROOT/bin/release" --mystery value
expect 'unknown option' 64 '' $'release: unknown option: --mystery\n'

run_cmd bash "$ROOT/bin/release" --channel stable -- ''
expect 'empty artifact' 64 '' $'release: artifact path must not be empty\n'

run_cmd env RELEASE_CHANNEL= RELEASE_ARTIFACT_ONE=dist/app.tgz make --no-print-directory -C "$ROOT" release
expect 'CI requires channel' 2 '' $'ci-release: RELEASE_CHANNEL is required\nmake: *** [Makefile:3: release] Error 64\n'

run_cmd bash "$ROOT/container/entrypoint.sh" inspect
expect 'container unknown command' 64 '' $'entrypoint: unknown command: inspect\n'

if [[ $fails -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all %d checks passed\n' "$checks"
exit 0
