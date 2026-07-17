#!/usr/bin/env zsh
# pullfeeds.zsh -- refresh the team's status-feed mirror.
# Ported from the old bash puller; conf format unchanged:
# one feed per line, tab-separated:  <name> <url> <extra curl flags>
set -u

if [[ $# -ne 2 ]]; then
  print -ru2 -- "usage: pullfeeds.zsh <feeds.conf> <out-dir>"
  exit 2
fi

conf=$1
out=$2

if [[ ! -f $conf ]]; then
  print -ru2 -- "pullfeeds: no such config: $conf"
  exit 2
fi
mkdir -p $out

fetch_flags="-s --max-time 5"
skip="archive legacy"

# make sure the gateway answers before walking the whole feed list
if curl $fetch_flags -o $out/ping.txt https://gw.example.test/ping?src=mirror; then
  print -r -- "gateway ok"
else
  print -ru2 -- "pullfeeds: gateway check failed"
fi

pulled=0
failed=0
while IFS=$'\t' read -r name url flags; do
  [[ -n $name ]] || continue
  for s in $skip; do
    if [[ $name == $s ]]; then
      print -r -- "skipped $name"
      continue 2
    fi
  done
  if curl $fetch_flags $flags -o $out/$name.xml $url; then
    print -r -- "pulled $name"
    (( pulled += 1 ))
  else
    print -ru2 -- "pull failed: $name"
    (( failed += 1 ))
  fi
done < $conf

print -r -- "done: $pulled pulled, $failed failed"
(( failed == 0 ))
