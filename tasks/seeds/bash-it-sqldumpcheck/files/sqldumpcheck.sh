#!/usr/bin/env bash

set -u
LC_ALL=C
export LC_ALL

program=${0##*/}

usage() {
  printf 'usage: %s [--max-age HOURS] DUMP\n' "$program" >&2
  exit 64
}

die() {
  printf '%s: error: %s\n' "$program" "$1" >&2
  exit 1
}

max_age_hours=24
while [ "$#" -gt 0 ]; do
  case $1 in
    --max-age)
      [ "$#" -ge 2 ] || usage
      max_age_hours=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      usage
      ;;
    *)
      break
      ;;
  esac
done

[ "$#" -eq 1 ] || usage
dump=$1

[[ $max_age_hours =~ ^[0-9]+$ ]] || die "max age must be a non-negative integer"
[ -f "$dump" ] || die "dump not found: $dump"
[ -r "$dump" ] || die "dump is not readable: $dump"

case $dump in
  *.sql.gz) compressed=yes ;;
  *.sql) compressed=no ;;
  *) die "unsupported dump suffix: $dump" ;;
esac

sidecar=$dump.sha256
[ -f "$sidecar" ] || die "missing checksum sidecar: $sidecar"
checksum_lines=()
mapfile -t checksum_lines < "$sidecar" || die "cannot read checksum sidecar: $sidecar"
[ "${#checksum_lines[@]}" -eq 1 ] || die "malformed checksum sidecar: $sidecar"
checksum_line=${checksum_lines[0]}
checksum_pattern='^([[:xdigit:]]{64})  (.+)$'
if [[ $checksum_line =~ $checksum_pattern ]]; then
  expected_digest=${BASH_REMATCH[1],,}
  recorded_name=${BASH_REMATCH[2]}
else
  die "malformed checksum sidecar: $sidecar"
fi

basename=${dump##*/}
[ "$recorded_name" = "$basename" ] || die "checksum sidecar names $recorded_name, expected $basename"
digest_output=$(sha256sum -- "$dump") || die "cannot checksum dump: $dump"
actual_digest=${digest_output%% *}
[ "$actual_digest" = "$expected_digest" ] || die "checksum mismatch: $dump"

mtime=$(stat -c '%Y' -- "$dump" 2>/dev/null) || die "cannot read dump age: $dump"
now=$(date +%s) || die "cannot read current time"
age_seconds=$((now - mtime))
max_age_seconds=$((10#$max_age_hours * 3600))
[ "$age_seconds" -le "$max_age_seconds" ] || die "dump is older than $max_age_hours hours: $dump"

content=$(mktemp "${TMPDIR:-/tmp}/sqldumpcheck.XXXXXX") || die "cannot create temporary file"
trap 'rm -f -- "$content"' EXIT

if [ "$compressed" = yes ]; then
  # Retain decoded bytes so the structural checks can report useful errors.
  gzip -dc -- "$dump" > "$content" 2>/dev/null || true
else
  cp -- "$dump" "$content" || die "cannot read dump: $dump"
fi

IFS= read -r header < "$content" || die "missing PostgreSQL dump header"
[ "$header" = '-- PostgreSQL database dump' ] || die "invalid PostgreSQL dump header"

grep -Eq '^-- Name: .+; Type: (SCHEMA|TABLE); Schema: .+; Owner: .*$' "$content" ||
  die "missing schema section"
grep -Eq '^-- Data for Name: .+; Type: TABLE DATA; Schema: .+; Owner: .*$' "$content" ||
  die "missing data section"

last_nonblank=$(awk 'NF { line = $0 } END { print line }' "$content")
[ "$last_nonblank" = '-- PostgreSQL database dump complete' ] ||
  die "missing PostgreSQL dump completion marker"

printf 'OK: %s\n' "$dump"
if [ "$compressed" = yes ]; then
  printf 'RESTORE_TEST: gzip -dc -- %q | psql --set=ON_ERROR_STOP=1 --single-transaction --dbname=restore_test\n' "$dump"
else
  printf 'RESTORE_TEST: psql --set=ON_ERROR_STOP=1 --single-transaction --dbname=restore_test --file=%q\n' "$dump"
fi
