#!/usr/bin/env bash

set -u
LC_ALL=C
export LC_ALL

usage() {
  printf 'usage: dnscache.sh <incident.tsv>\n' >&2
}

die_data() {
  printf 'dnscache.sh: malformed incident: %s\n' "$1" >&2
  exit 65
}

resolver_flush_command() {
  # The report is reviewed before an operator executes any remediation.
  printf 'rndc flush\n'
}

if (( $# != 1 )); then
  usage
  exit 64
fi

if [[ ! -r $1 ]]; then
  printf 'dnscache.sh: cannot read: %s\n' "$1" >&2
  exit 66
fi

exec 3< "$1" || {
  printf 'dnscache.sh: cannot read: %s\n' "$1" >&2
  exit 66
}

IFS= read -r header <&3 || die_data 'missing header'
expected_header=$'record\tttl\tchanged_at\tobserved_at\tprevious\tauthoritative\tresolver\tclient'
[[ $header == "$expected_header" ]] || die_data 'unexpected header'

IFS=$'\t' read -r record ttl_text changed_text observed_text previous authoritative resolver client extra <&3 \
  || die_data 'missing data row'
[[ -z ${extra:-} ]] || die_data 'too many fields'
[[ -n $record && -n $ttl_text && -n $changed_text && -n $observed_text \
  && -n $previous && -n $authoritative && -n $resolver && -n $client ]] \
  || die_data 'empty field'

if IFS= read -r trailing <&3; then
  die_data 'expected exactly one data row'
fi
exec 3<&-

[[ $record =~ ^[A-Za-z0-9_.-]+$ ]] || die_data 'invalid record name'
[[ $ttl_text =~ ^[0-9]+$ ]] || die_data 'ttl is not an integer'
[[ $changed_text =~ ^[0-9]+$ ]] || die_data 'changed_at is not an integer'
[[ $observed_text =~ ^[0-9]+$ ]] || die_data 'observed_at is not an integer'

ttl=$((10#$ttl_text))
changed_at=$((10#$changed_text))
observed_at=$((10#$observed_text))
(( ttl > 0 )) || die_data 'ttl must be positive'
(( observed_at >= changed_at )) || die_data 'observation predates record change'

expected_expiry=$((changed_at + ttl))
ttl_age=$((observed_at - changed_at))
if (( observed_at < expected_expiry )); then
  ttl_remaining=$((expected_expiry - observed_at))
else
  ttl_remaining=0
fi

if [[ $resolver == "$authoritative" ]]; then
  if [[ $client == "$authoritative" ]]; then
    if (( observed_at >= expected_expiry )); then
      cache_state='expired-and-refreshed'
    else
      cache_state='authoritative-value-observed'
    fi
    action='none'
    flush_scope='none'
  else
    cache_state='client-only-stale'
    action="clear-client-name $record"
    flush_scope="client-name:$record"
  fi
else
  if [[ $resolver == "$previous" && $client == "$previous" ]]; then
    if (( observed_at < expected_expiry )); then
      cache_state='old-answer-within-ttl'
    else
      cache_state='old-answer-past-ttl'
    fi
  else
    cache_state='resolver-answer-mismatch'
  fi
  action=$(resolver_flush_command "$record")
  flush_scope="resolver-name:$record"
fi

printf 'record=%s\n' "$record"
printf 'ttl_seconds=%s\n' "$ttl"
printf 'record_changed_at=%s\n' "$changed_at"
printf 'observed_at=%s\n' "$observed_at"
printf 'expected_expiry=%s\n' "$expected_expiry"
printf 'ttl_age_seconds=%s\n' "$ttl_age"
printf 'ttl_remaining_seconds=%s\n' "$ttl_remaining"
printf 'authoritative=%s\n' "$authoritative"
printf 'resolver=%s\n' "$resolver"
printf 'client=%s\n' "$client"
printf 'cache_state=%s\n' "$cache_state"
printf 'action=%s\n' "$action"
printf 'flush_scope=%s\n' "$flush_scope"
printf 'verify_after=%s; authoritative,resolver,client must agree on %s\n' \
  "$expected_expiry" "$authoritative"
printf 'broader_clear=unnecessary; evidence concerns only %s; unrelated cache entries are not implicated\n' \
  "$record"
