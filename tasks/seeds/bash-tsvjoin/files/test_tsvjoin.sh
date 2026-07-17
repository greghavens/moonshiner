#!/usr/bin/env bash
# Acceptance harness for tsvjoin.sh.
# Run from the workspace root:  bash test_tsvjoin.sh
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

if [[ ! -f tsvjoin.sh ]]; then
  printf 'FAIL tsvjoin.sh not found in the workspace root\n'
  exit 1
fi

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

# ---- fixtures ---------------------------------------------------------------

# Default-key pair: key = first column of the left file ("order").
printf 'order\titem\tqty\no1\twidget\t2\no3\tgadget\t1\no2\twidget\t5\n' > "$T/orders.tsv"
printf 'order\tstate\no1\tshipped\no3\tpacked\n' > "$T/status.tsv"

# --key pair: key sits at column 2 of the left file and column 3 of the right.
# Right has two rows for A-10 (duplicate key) and a row (Z-99) with no left match.
printf 'shipment\tsku\tqty\ns1\tB-77\t3\ns2\tA-10\t1\ns3\tB-77\t2\n' > "$T/shipments.tsv"
printf 'title\tbin\tsku\nWidget classic\tR1\tA-10\nWidget pro\tR2\tA-10\nCable pack\tR9\tZ-99\n' > "$T/catalog.tsv"

# Header-only variants and a zero-byte file.
printf 'title\tbin\tsku\n' > "$T/catalog_empty.tsv"
printf 'order\titem\tqty\n' > "$T/orders_empty.tsv"
printf 'order\tstate\n' > "$T/status_empty.tsv"
: > "$T/zero.tsv"

# ---- default key: left join, fill, key-sorted body ----------------------------

printf -v exp_default 'order\titem\tqty\tstate\no1\twidget\t2\tshipped\no2\twidget\t5\t-\no3\tgadget\t1\tpacked\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" orders.tsv status.tsv
expect "default key (first left column)" 0 "$exp_default" ""

# ---- --key: differing key positions, duplicate right keys ---------------------

printf -v exp_key 'shipment\tsku\tqty\ttitle\tbin\ns2\tA-10\t1\tWidget classic\tR1\ns2\tA-10\t1\tWidget pro\tR2\ns1\tB-77\t3\t-\t-\ns3\tB-77\t2\t-\t-\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" --key sku shipments.tsv catalog.tsv
expect "--key sku, duplicate keys and fills" 0 "$exp_key" ""
first_out=$OUT

run_in "$T" bash "$ROOT/tsvjoin.sh" --key sku shipments.tsv catalog.tsv
assert_eq "output is byte-stable across runs" "$first_out" "$OUT"

# ---- header-only right file: every left row is filled --------------------------

printf -v exp_rempty 'shipment\tsku\tqty\ttitle\tbin\ns2\tA-10\t1\t-\t-\ns1\tB-77\t3\t-\t-\ns3\tB-77\t2\t-\t-\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" --key sku shipments.tsv catalog_empty.tsv
expect "header-only right file" 0 "$exp_rempty" ""

# ---- header-only left file: just the joined header -----------------------------

printf -v exp_lempty 'order\titem\tqty\tstate\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" orders_empty.tsv status.tsv
expect "header-only left file" 0 "$exp_lempty" ""

run_in "$T" bash "$ROOT/tsvjoin.sh" orders_empty.tsv status_empty.tsv
expect "both files header-only" 0 "$exp_lempty" ""

# ---- key column missing ---------------------------------------------------------

run_in "$T" bash "$ROOT/tsvjoin.sh" --key bin shipments.tsv catalog.tsv
expect "key column missing from the left file" 65 "" 'tsvjoin.sh: no such column: bin in shipments.tsv'$'\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" --key qty shipments.tsv catalog.tsv
expect "key column missing from the right file" 65 "" 'tsvjoin.sh: no such column: qty in catalog.tsv'$'\n'

# ---- zero-byte files -------------------------------------------------------------

run_in "$T" bash "$ROOT/tsvjoin.sh" zero.tsv status.tsv
expect "zero-byte left file" 65 "" 'tsvjoin.sh: empty file: zero.tsv'$'\n'

run_in "$T" bash "$ROOT/tsvjoin.sh" orders.tsv zero.tsv
expect "zero-byte right file" 65 "" 'tsvjoin.sh: empty file: zero.tsv'$'\n'

# ---- unreadable file --------------------------------------------------------------

run_in "$T" bash "$ROOT/tsvjoin.sh" orders.tsv nope.tsv
expect "missing right file" 66 "" 'tsvjoin.sh: cannot read: nope.tsv'$'\n'

# ---- usage ------------------------------------------------------------------------

printf -v exp_usage 'usage: tsvjoin.sh [--key NAME] <left.tsv> <right.tsv>\n'

run_in "$T" bash "$ROOT/tsvjoin.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/tsvjoin.sh" orders.tsv
expect "one file only" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/tsvjoin.sh" orders.tsv status.tsv extra.tsv
expect "three files" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/tsvjoin.sh" --key
expect "--key without a value" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/tsvjoin.sh" --on sku orders.tsv status.tsv
expect "unknown flag" 64 "" "$exp_usage"

# ---- summary -----------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
