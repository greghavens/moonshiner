#!/usr/bin/env bash
# runjobs.sh -- apply the day's filing jobs to the drop folder.
#
# Job list is tab-separated: <action> <name> <flags>
#   copy  copy <src>/<name> to <out>/<name>          (flags passed to cp)
#   pack  gzip <src>/<name> to <out>/<name>.gz       (flags passed to gzip)
#   note  append "filed: <name>" to <out>/filing.log
set -u
LC_ALL=C
export LC_ALL

if [ $# -ne 3 ]; then
  echo "usage: runjobs.sh <jobs.tsv> <src-dir> <out-dir>" >&2
  exit 2
fi

jobs=$1
src=$2
out=$3

if [ ! -f "$jobs" ]; then
  echo "runjobs: no such job list: $jobs" >&2
  exit 2
fi
if [ ! -d "$src" ]; then
  echo "runjobs: no such source dir: $src" >&2
  exit 2
fi
mkdir -p "$out"

ok=0
failed=0
while IFS=$'\t' read -r action name flags; do
  [ -n "$action" ] || continue
  case $action in
    copy)
      cmd="cp $flags -- '$src/$name' '$out/$name'"
      ;;
    pack)
      cmd="gzip $flags -n -c -- '$src/$name' > '$out/$name.gz'"
      ;;
    note)
      cmd="printf '%s\n' 'filed: $name' >> '$out/filing.log'"
      ;;
    *)
      echo "runjobs: unknown action: $action" >&2
      failed=$((failed + 1))
      continue
      ;;
  esac
  if eval "$cmd" 2>/dev/null; then
    ok=$((ok + 1))
  else
    echo "runjobs: job failed: $action $name" >&2
    failed=$((failed + 1))
  fi
done < "$jobs"

echo "jobs ok: $ok"
echo "jobs failed: $failed"
[ "$failed" -eq 0 ]
