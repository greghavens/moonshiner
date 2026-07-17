#!/usr/bin/env zsh
# printsum.zsh — nightly print-accounting summary over the print-server feed.
#
# Feed: one job per line,  printer|user|pages  (pipe-separated, three fields).
# Lines starting with # and blank lines are exporter padding: skipped quietly.
# Any other field count is malformed: warn on stderr, skip, keep going.
#
# WATCH: printers billed back to their owning departments. Space-separated,
# reported in exactly this order.
WATCH="annex lobby-a"

usage() {
  echo "usage: printsum.zsh <feed-file>" >&2
  exit 64
}

[ "$#" -eq 1 ] || usage
feed=$1
if [ ! -f "$feed" ] || [ ! -r "$feed" ]; then
  echo "printsum.zsh: cannot read: $feed" >&2
  exit 66
fi

declare -A jobs_by pages_by users_seen
declare -a seen
declare -i lineno=0 jobs_total=0 pages_total=0

while IFS= read -r line; do
  lineno=$(( lineno + 1 ))
  [ -z "$line" ] && continue
  case $line in '#'*) continue ;; esac
  f=($(printf '%s\n' "$line" | tr '|' ' '))
  if [ "${#f[@]}" -ne 3 ]; then
    echo "printsum.zsh: line $lineno: malformed, skipped" >&2
    continue
  fi
  printer=${f[0]}
  user=${f[1]}
  pages=${f[2]}
  if [ -z "${jobs_by[$printer]:-}" ]; then
    seen+=("$printer")
  fi
  jobs_by[$printer]=$(( ${jobs_by[$printer]:-0} + 1 ))
  pages_by[$printer]=$(( ${pages_by[$printer]:-0} + pages ))
  users_seen[$user]=1
  jobs_total=$(( jobs_total + 1 ))
  pages_total=$(( pages_total + pages ))
done < "$feed"

echo "USAGE"
if [ "${#seen[@]}" -gt 0 ]; then
  printf '%s\n' "${seen[@]}" | LC_ALL=C sort | while IFS= read -r p; do
    printf '%s\t%s\t%s\n' "$p" "${jobs_by[$p]}" "${pages_by[$p]}"
  done
fi
echo "WATCHED"
for w in $WATCH; do
  printf '%s\t%s\n' "$w" "${pages_by[$w]:-0}"
done
echo "TOTALS"
printf 'jobs\t%s\n' "$jobs_total"
printf 'printers\t%s\n' "${#seen[@]}"
printf 'users\t%s\n' "${#users_seen[@]}"
printf 'pages\t%s\n' "$pages_total"
