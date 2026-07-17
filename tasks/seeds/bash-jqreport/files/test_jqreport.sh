#!/usr/bin/env bash
# Acceptance harness for report.sh.
# Run from the workspace root:  bash test_jqreport.sh
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
cleanup() { rm -rf "$ROOT/$T"; }
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

if [[ ! -f report.sh ]]; then
  printf 'FAIL report.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# ---- fixtures ---------------------------------------------------------------

printf '%s\n' \
  '{' \
  '  "orders": [' \
  '    {"id": "o-1001", "region": "west",' \
  '     "items": [' \
  '       {"sku": "A-10", "qty": 2, "unit_price": 350, "discount": 40},' \
  '       {"sku": "B-77", "qty": 1, "unit_price": 1200}' \
  '     ]},' \
  '    {"id": "o-1002", "region": "east",' \
  '     "items": [' \
  '       {"sku": "A-10", "qty": 1, "unit_price": 350},' \
  '       {"sku": "A-10", "qty": 3, "unit_price": 300, "discount": 50}' \
  '     ]},' \
  '    {"id": "o-1003",' \
  '     "items": [' \
  '       {"sku": "C-05", "qty": 4, "unit_price": 125}' \
  '     ]},' \
  '    {"id": "o-1004", "region": "west"},' \
  '    {"id": "o-1005", "region": "east", "items": []}' \
  '  ]' \
  '}' \
  > "$T/orders1.json"

printf '%s\n' \
  '{"orders": [' \
  '  {"id": "a-1", "items": [{"sku": "K-01", "qty": 1, "unit_price": 999}]},' \
  '  {"id": "a-2"},' \
  '  {"id": "a-3", "region": "north"}' \
  ']}' \
  > "$T/orders2.json"

printf '{"orders": []}\n' > "$T/orders3.json"
printf '{}\n' > "$T/orders4.json"
printf '{"orders": [\n' > "$T/broken.json"

# ---- skus report ---------------------------------------------------------------

printf -v exp_skus1 'sku\tunits\trevenue\torders\nA-10\t6\t1860\t2\nB-77\t1\t1200\t1\nC-05\t4\t500\t1\n'

run_in "$T" bash "$ROOT/report.sh" skus orders1.json
expect "skus report, rich document" 0 "$exp_skus1" ""

printf -v exp_skus2 'sku\tunits\trevenue\torders\nK-01\t1\t999\t1\n'

run_in "$T" bash "$ROOT/report.sh" skus orders2.json
expect "skus report, sparse document" 0 "$exp_skus2" ""

printf -v exp_skus_hdr 'sku\tunits\trevenue\torders\n'

run_in "$T" bash "$ROOT/report.sh" skus orders3.json
expect "skus report, empty orders array" 0 "$exp_skus_hdr" ""

run_in "$T" bash "$ROOT/report.sh" skus orders4.json
expect "skus report, no orders key at all" 0 "$exp_skus_hdr" ""

# ---- regions report -------------------------------------------------------------

printf -v exp_reg1 'region\torders\tunits\trevenue\n\t1\t4\t500\neast\t2\t4\t1200\nwest\t2\t3\t1860\n'

run_in "$T" bash "$ROOT/report.sh" regions orders1.json
expect "regions report, rich document" 0 "$exp_reg1" ""

printf -v exp_reg2 'region\torders\tunits\trevenue\n\t2\t1\t999\nnorth\t1\t0\t0\n'

run_in "$T" bash "$ROOT/report.sh" regions orders2.json
expect "regions report, sparse document" 0 "$exp_reg2" ""

printf -v exp_reg_hdr 'region\torders\tunits\trevenue\n'

run_in "$T" bash "$ROOT/report.sh" regions orders3.json
expect "regions report, empty orders array" 0 "$exp_reg_hdr" ""

run_in "$T" bash "$ROOT/report.sh" regions orders4.json
expect "regions report, no orders key at all" 0 "$exp_reg_hdr" ""

# ---- broken input ----------------------------------------------------------------

run_in "$T" bash "$ROOT/report.sh" skus broken.json
expect "invalid JSON" 65 "" 'report.sh: invalid JSON: broken.json'$'\n'

run_in "$T" bash "$ROOT/report.sh" regions nope.json
expect "missing file" 66 "" 'report.sh: cannot read: nope.json'$'\n'

# ---- usage -----------------------------------------------------------------------

printf -v exp_usage 'usage: report.sh <skus|regions> <file.json>\n'

run_in "$T" bash "$ROOT/report.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/report.sh" skus
expect "missing file argument" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/report.sh" daily orders1.json
expect "unknown mode" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/report.sh" skus orders1.json extra
expect "too many arguments" 64 "" "$exp_usage"

# ---- summary -----------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
