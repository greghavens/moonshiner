#!/usr/bin/env zsh
# collect_run.zsh -- pull finished frames off the scan bench into a run folder.
#
# The bench software drops finished frames into <bench>/color and <bench>/mono.
# Collecting a run copies every frame into <run> as <subdir>__<name>, writes a
# sorted index.txt, and reports how many frames moved. A run folder is
# collected once: index.txt marks it done, and part_<digit>.lst leftovers from
# a crashed collect mean the folder needs a manual sweep first.
#
# usage: collect_run.zsh <bench-dir> <run-dir>
emulate -R zsh
setopt no_unset

if (( $# != 2 )); then
  print -u2 "usage: collect_run.zsh <bench-dir> <run-dir>"
  exit 64
fi

bench=$1
run=$2

if [[ ! -d $bench/color || ! -d $bench/mono ]]; then
  print -u2 "collect_run.zsh: not a scan bench: $bench"
  exit 66
fi

mkdir -p -- $run

if [[ -e $run/index.txt ]]; then
  print -u2 "collect_run.zsh: already collected: $run"
  exit 65
fi

# A crashed collect leaves part_<digit>.lst behind; refuse to mix two runs.
if [ -e $run/part_?.lst ]; then
  print -u2 "collect_run.zsh: partial batch in $run, sweep it first"
  exit 65
fi

typeset -a collected
for f in $bench/{color,mono}(.); do
  rel=${f#$bench/}
  dest=$run/${rel:h}__${rel:t}
  cp -- $f $dest
  collected+=( ${dest:t} )
done

if (( ${#collected} == 0 )); then
  print -u2 "collect_run.zsh: nothing to collect"
  exit 1
fi

print -rl -- ${(o)collected} > $run/index.txt
print "collected: ${#collected} frame(s)"
