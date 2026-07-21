#!/usr/bin/env bash

set -u
LC_ALL=C
export LC_ALL

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
checker=$root/sqldumpcheck.sh
scratch=$(mktemp -d "$root/.sqldumpcheck-test.XXXXXX") || exit 1
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/tmp"
export TMPDIR=$scratch/tmp

tests=0
failures=0

fail() {
  printf 'not ok %d - %s: %s\n' "$tests" "$1" "$2"
  failures=$((failures + 1))
}

make_dump() {
  local path=$1 mode=${2:-valid}
  {
    if [ "$mode" = bad-header ]; then
      printf '%s\n' '-- not a PostgreSQL dump'
    else
      printf '%s\n' '-- PostgreSQL database dump'
    fi
    printf '%s\n' '-- Dumped from database version 16.2'
    if [ "$mode" != no-schema ]; then
      printf '%s\n' '-- Name: widgets; Type: TABLE; Schema: public; Owner: backup'
      printf '%s\n' 'CREATE TABLE public.widgets (id integer, name text);'
    fi
    if [ "$mode" != no-data ]; then
      printf '%s\n' '-- Data for Name: widgets; Type: TABLE DATA; Schema: public; Owner: backup'
      printf '%s\n' 'COPY public.widgets (id, name) FROM stdin;' '1 widget' '\\.'
    fi
    if [ "$mode" != no-completion ]; then
      printf '\n%s\n' '-- PostgreSQL database dump complete'
    fi
  } > "$path"
}

write_checksum() {
  local file=$1 digest
  digest=$(sha256sum -- "$file") || exit 1
  digest=${digest%% *}
  printf '%s  %s\n' "$digest" "${file##*/}" > "$file.sha256"
}

expect_failure() {
  local label=$1 needle=$2
  shift 2
  tests=$((tests + 1))
  : > "$scratch/stdout"
  : > "$scratch/stderr"
  if "$@" > "$scratch/stdout" 2> "$scratch/stderr"; then
    fail "$label" 'command unexpectedly succeeded'
    return
  fi
  if [ -s "$scratch/stdout" ]; then
    fail "$label" 'failure emitted stdout/restore command'
    return
  fi
  if ! grep -Fq -- "$needle" "$scratch/stderr"; then
    fail "$label" "stderr did not contain: $needle"
    return
  fi
  printf 'ok %d - %s\n' "$tests" "$label"
}

expect_plain_success() {
  local label=$1 file=$2 output expected quoted
  tests=$((tests + 1))
  if ! output=$(bash "$checker" --max-age 24 "$file" 2> "$scratch/stderr"); then
    fail "$label" "$(< "$scratch/stderr")"
    return
  fi
  printf -v quoted '%q' "$file"
  expected=$(printf 'OK: %s\nRESTORE_TEST: psql --set=ON_ERROR_STOP=1 --single-transaction --dbname=restore_test --file=%s' "$file" "$quoted")
  if [ "$output" != "$expected" ]; then
    fail "$label" "unexpected output: $output"
    return
  fi
  printf 'ok %d - %s\n' "$tests" "$label"
}

expect_gzip_success() {
  local label=$1 file=$2 output expected quoted
  tests=$((tests + 1))
  if ! output=$(bash "$checker" --max-age 24 "$file" 2> "$scratch/stderr"); then
    fail "$label" "$(< "$scratch/stderr")"
    return
  fi
  printf -v quoted '%q' "$file"
  expected=$(printf 'OK: %s\nRESTORE_TEST: gzip -dc -- %s | psql --set=ON_ERROR_STOP=1 --single-transaction --dbname=restore_test' "$file" "$quoted")
  if [ "$output" != "$expected" ]; then
    fail "$label" "unexpected output: $output"
    return
  fi
  printf 'ok %d - %s\n' "$tests" "$label"
}

plain="$scratch/nightly dump.sql"
make_dump "$plain"
write_checksum "$plain"
expect_plain_success 'valid plain dump and restore command' "$plain"

gzip_dump="$scratch/nightly dump.sql.gz"
gzip -n -c -- "$plain" > "$gzip_dump"
write_checksum "$gzip_dump"
expect_gzip_success 'valid gzip dump and restore pipeline' "$gzip_dump"

bad_header=$scratch/bad-header.sql
make_dump "$bad_header" bad-header
write_checksum "$bad_header"
expect_failure 'rejects bad header' 'invalid PostgreSQL dump header' bash "$checker" "$bad_header"

no_schema=$scratch/no-schema.sql
make_dump "$no_schema" no-schema
write_checksum "$no_schema"
expect_failure 'rejects missing schema section' 'missing schema section' bash "$checker" "$no_schema"

no_data=$scratch/no-data.sql
make_dump "$no_data" no-data
write_checksum "$no_data"
expect_failure 'rejects missing data section' 'missing data section' bash "$checker" "$no_data"

truncated_plain=$scratch/truncated.sql
make_dump "$truncated_plain" no-completion
write_checksum "$truncated_plain"
expect_failure 'rejects truncated plain dump' 'missing PostgreSQL dump completion marker' bash "$checker" "$truncated_plain"

mismatch=$scratch/mismatch.sql
make_dump "$mismatch"
write_checksum "$mismatch"
printf '%s\n' '-- changed after hashing' >> "$mismatch"
expect_failure 'rejects checksum mismatch' 'checksum mismatch' bash "$checker" "$mismatch"

wrong_name=$scratch/wrong-name.sql
make_dump "$wrong_name"
wrong_digest=$(sha256sum -- "$wrong_name")
wrong_digest=${wrong_digest%% *}
printf '%s  %s\n' "$wrong_digest" 'another.sql' > "$wrong_name.sha256"
expect_failure 'rejects checksum sidecar for another dump' 'checksum sidecar names another.sql' bash "$checker" "$wrong_name"

malformed_sidecar=$scratch/malformed-sidecar.sql
make_dump "$malformed_sidecar"
malformed_digest=$(sha256sum -- "$malformed_sidecar")
malformed_digest=${malformed_digest%% *}
printf '%s\t%s\n' "$malformed_digest" "${malformed_sidecar##*/}" > "$malformed_sidecar.sha256"
expect_failure 'rejects malformed checksum sidecar' 'malformed checksum sidecar' bash "$checker" "$malformed_sidecar"

stale=$scratch/stale.sql
make_dump "$stale"
write_checksum "$stale"
touch -t 200001010000 -- "$stale"
expect_failure 'rejects stale dump' 'dump is older than 24 hours' bash "$checker" --max-age 24 "$stale"

complete_for_truncation=$scratch/complete-for-truncation.sql
make_dump "$complete_for_truncation"
complete_gzip=$scratch/complete.sql.gz
gzip -n -c -- "$complete_for_truncation" > "$complete_gzip"
gzip_size=$(wc -c < "$complete_gzip")
truncated_gzip=$scratch/truncated.sql.gz
dd if="$complete_gzip" of="$truncated_gzip" bs=1 count=$((gzip_size - 4)) status=none
write_checksum "$truncated_gzip"
expect_failure 'rejects truncated gzip even when stored checksum matches' 'invalid gzip stream' bash "$checker" "$truncated_gzip"

if [ "$failures" -ne 0 ]; then
  printf '%d of %d tests failed\n' "$failures" "$tests" >&2
  exit 1
fi

printf 'all %d tests passed\n' "$tests"
