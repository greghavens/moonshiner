#!/usr/bin/env bash
# Regression harness for the rollout orchestrator.
# Run from the workspace root:  bash test_rollout.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
cleanup() { rm -rf "$ROOT/$T" "$ROOT/src" "$ROOT/migrations" "$ROOT/logs" "$ROOT/out"; }
trap cleanup EXIT

checks=0
fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_true() { # assert_true <label> <rc-of-condition>
  checks=$((checks + 1))
  if [[ "$2" -eq 0 ]]; then
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
}

slurp() { # slurp <var> <file> -- byte-exact file contents into var
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_in() { # run_in <dir> <cmd...> -- capture RC, OUT, ERR byte-exactly
  local d=$1
  shift
  ( cd "$d" && exec "$@" ) > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  slurp OUT "$ROOT/$T/out"
  slurp ERR "$ROOT/$T/err"
}

if [[ ! -f rollout.sh || ! -f steps/build.sh || ! -f steps/migrate.sh || ! -f steps/smoke.sh ]]; then
  printf 'FAIL rollout.sh and the steps/ scripts must exist in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

assert_file() { # assert_file <label> <expected> <path>
  local got=''
  if [[ -f "$3" ]]; then
    slurp got "$3"
  fi
  assert_eq "$1" "$2" "$got"
}

fresh_fixture() {
  rm -rf "$ROOT/src" "$ROOT/migrations" "$ROOT/logs" "$ROOT/out"
  mkdir -p "$ROOT/src" "$ROOT/migrations"
  printf 'alpha component\n' > "$ROOT/src/a.txt"
  printf 'bravo component\n' > "$ROOT/src/b.txt"
  printf 'create table bins (id int);\n' > "$ROOT/migrations/001_init.sql"
  printf 'alter table bins add column label text;\n' > "$ROOT/migrations/002_add_bins.sql"
}

# ---- clean deploy: every step passes ------------------------------------------

printf -v exp_ok_out '%s\n' \
  'rollout: running build' \
  'build: scanning 2 source file(s)' \
  'build: bundled 2 file(s)' \
  'rollout: running migrate' \
  'migrate: applying 001_init.sql' \
  'migrate: applying 002_add_bins.sql' \
  'migrate: 2 migration(s) applied' \
  'rollout: running smoke' \
  'smoke: bundle looks good' \
  'smoke: schema log present' \
  '==== rollout summary ====' \
  'build: OK' \
  'migrate: OK' \
  'smoke: OK'

fresh_fixture
run_in "$ROOT" bash rollout.sh
expect "clean deploy" 0 "$exp_ok_out" ""

printf -v exp_bundle '== bundle ==\nalpha component\nbravo component\n'
assert_file "clean deploy: bundle written" "$exp_bundle" "$ROOT/out/bundle.txt"

printf -v exp_schema '001_init.sql\n002_add_bins.sql\n'
assert_file "clean deploy: schema log written" "$exp_schema" "$ROOT/out/schema.log"

printf -v exp_build_log 'build: scanning 2 source file(s)\nbuild: bundled 2 file(s)\n'
assert_file "clean deploy: logs/build.log mirrors the step" "$exp_build_log" "$ROOT/logs/build.log"

printf -v exp_migrate_log 'migrate: applying 001_init.sql\nmigrate: applying 002_add_bins.sql\nmigrate: 2 migration(s) applied\n'
assert_file "clean deploy: logs/migrate.log mirrors the step" "$exp_migrate_log" "$ROOT/logs/migrate.log"

printf -v exp_smoke_log 'smoke: bundle looks good\nsmoke: schema log present\n'
assert_file "clean deploy: logs/smoke.log mirrors the step" "$exp_smoke_log" "$ROOT/logs/smoke.log"

# ---- deploy with a broken build: the summary must tell the truth ----------------

printf -v exp_bad_out '%s\n' \
  'rollout: running build' \
  'build: scanning 2 source file(s)' \
  'build: error: empty source file: src/b.txt' \
  'rollout: running migrate' \
  'migrate: applying 001_init.sql' \
  'migrate: applying 002_add_bins.sql' \
  'migrate: 2 migration(s) applied' \
  'rollout: running smoke' \
  'smoke: error: out/bundle.txt is missing' \
  '==== rollout summary ====' \
  'build: FAIL (exit 3)' \
  'migrate: OK' \
  'smoke: FAIL (exit 5)'

fresh_fixture
: > "$ROOT/src/b.txt"
run_in "$ROOT" bash rollout.sh
expect "failing build is reported" 1 "$exp_bad_out" ""

[[ ! -e "$ROOT/out/bundle.txt" ]]; assert_true "failing build: no bundle produced" "$?"

printf -v exp_bad_build_log 'build: scanning 2 source file(s)\nbuild: error: empty source file: src/b.txt\n'
assert_file "failing build: error still lands in logs/build.log" "$exp_bad_build_log" "$ROOT/logs/build.log"

assert_file "failing build: logs/migrate.log still mirrors the step" "$exp_migrate_log" "$ROOT/logs/migrate.log"

printf -v exp_bad_smoke_log 'smoke: error: out/bundle.txt is missing\n'
assert_file "failing build: logs/smoke.log still mirrors the step" "$exp_bad_smoke_log" "$ROOT/logs/smoke.log"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
