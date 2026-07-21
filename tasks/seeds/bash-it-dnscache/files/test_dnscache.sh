#!/usr/bin/env bash

set -u
LC_ALL=C
export LC_ALL

failures=0
header=$'record\tttl\tchanged_at\tobserved_at\tprevious\tauthoritative\tresolver\tclient'

fail() {
  printf 'not ok - %s\n' "$1"
  failures=$((failures + 1))
}

pass() {
  printf 'ok - %s\n' "$1"
}

field() {
  local report=$1 wanted=$2 key value
  while IFS='=' read -r key value; do
    if [[ $key == "$wanted" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done <<< "$report"
  return 1
}

assert_field() {
  local label=$1 report=$2 key=$3 expected=$4 actual
  if ! actual=$(field "$report" "$key"); then
    fail "$label: missing $key"
  elif [[ $actual != "$expected" ]]; then
    fail "$label: $key expected [$expected], got [$actual]"
  else
    pass "$label: $key"
  fi
}

assert_report() {
  local label=$1 actual=$2 expected=$3
  if [[ $actual != "$expected" ]]; then
    fail "$label: report content or key order changed"
  else
    pass "$label: report content and key order"
  fi
}

run_snapshot() {
  local row=$1
  bash dnscache.sh <(printf '%s\n%s\n' "$header" "$row")
}

within=$(run_snapshot $'api.service.test.\t300\t1700000000\t1700000120\t192.0.2.10\t203.0.113.42\t192.0.2.10\t192.0.2.10') || {
  fail 'within-TTL snapshot runs'
  within=''
}
expected_within=$'record=api.service.test.\nttl_seconds=300\nrecord_changed_at=1700000000\nobserved_at=1700000120\nexpected_expiry=1700000300\nttl_age_seconds=120\nttl_remaining_seconds=180\nauthoritative=203.0.113.42\nresolver=192.0.2.10\nclient=192.0.2.10\ncache_state=old-answer-within-ttl\naction=rndc flushname api.service.test.\nflush_scope=resolver-name:api.service.test.\nverify_after=1700000300; authoritative,resolver,client must agree on 203.0.113.42\nbroader_clear=unnecessary; evidence concerns only api.service.test.; unrelated cache entries are not implicated'
assert_report 'within TTL' "$within" "$expected_within"
assert_field 'within TTL' "$within" cache_state 'old-answer-within-ttl'
assert_field 'within TTL' "$within" expected_expiry '1700000300'
assert_field 'within TTL' "$within" ttl_remaining_seconds '180'
assert_field 'within TTL' "$within" action 'rndc flushname api.service.test.'
assert_field 'within TTL' "$within" flush_scope 'resolver-name:api.service.test.'
assert_field 'within TTL' "$within" broader_clear \
  'unnecessary; evidence concerns only api.service.test.; unrelated cache entries are not implicated'

boundary=$(run_snapshot $'api.service.test.\t300\t1700000000\t1700000300\t192.0.2.10\t203.0.113.42\t203.0.113.42\t203.0.113.42') || {
  fail 'expiry-boundary snapshot runs'
  boundary=''
}
assert_field 'expiry boundary' "$boundary" cache_state 'expired-and-refreshed'
assert_field 'expiry boundary' "$boundary" ttl_remaining_seconds '0'
assert_field 'expiry boundary' "$boundary" action 'none'
assert_field 'expiry boundary' "$boundary" flush_scope 'none'

stale_boundary=$(run_snapshot $'api.service.test.\t300\t1700000000\t1700000300\t192.0.2.10\t203.0.113.42\t192.0.2.10\t192.0.2.10') || {
  fail 'stale expiry-boundary snapshot runs'
  stale_boundary=''
}
assert_field 'stale expiry boundary' "$stale_boundary" cache_state 'old-answer-past-ttl'
assert_field 'stale expiry boundary' "$stale_boundary" ttl_remaining_seconds '0'
assert_field 'stale expiry boundary' "$stale_boundary" action 'rndc flushname api.service.test.'
assert_field 'stale expiry boundary' "$stale_boundary" flush_scope 'resolver-name:api.service.test.'

past=$(run_snapshot $'api.service.test.\t300\t1700000000\t1700000301\t192.0.2.10\t203.0.113.42\t192.0.2.10\t192.0.2.10') || {
  fail 'past-TTL snapshot runs'
  past=''
}
assert_field 'past TTL' "$past" cache_state 'old-answer-past-ttl'
assert_field 'past TTL' "$past" ttl_age_seconds '301'
assert_field 'past TTL' "$past" ttl_remaining_seconds '0'
assert_field 'past TTL' "$past" action 'rndc flushname api.service.test.'
assert_field 'past TTL' "$past" flush_scope 'resolver-name:api.service.test.'

client_only=$(run_snapshot $'api.service.test.\t300\t1700000000\t1700000301\t192.0.2.10\t203.0.113.42\t203.0.113.42\t192.0.2.10') || {
  fail 'client-only snapshot runs'
  client_only=''
}
assert_field 'client only' "$client_only" cache_state 'client-only-stale'
assert_field 'client only' "$client_only" action 'clear-client-name api.service.test.'
assert_field 'client only' "$client_only" flush_scope 'client-name:api.service.test.'

other_record=$(run_snapshot $'db.internal.test.\t60\t1700001000\t1700001030\t192.0.2.20\t203.0.113.99\t198.51.100.8\t203.0.113.99') || {
  fail 'other-record resolver-mismatch snapshot runs'
  other_record=''
}
assert_field 'other record' "$other_record" cache_state 'resolver-answer-mismatch'
assert_field 'other record' "$other_record" action 'rndc flushname db.internal.test.'
assert_field 'other record' "$other_record" flush_scope 'resolver-name:db.internal.test.'
assert_field 'other record' "$other_record" broader_clear \
  'unnecessary; evidence concerns only db.internal.test.; unrelated cache entries are not implicated'

if (( failures > 0 )); then
  printf '%s test(s) failed\n' "$failures" >&2
  exit 1
fi

printf 'all dnscache tests passed\n'
