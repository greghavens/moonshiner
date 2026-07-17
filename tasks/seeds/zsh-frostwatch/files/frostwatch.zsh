#!/usr/bin/env zsh
# frostwatch.zsh — overnight frost report for the nursery beds.
#
# usage: frostwatch.zsh TEMPLOG [BED]
#   TEMPLOG  one reading per line: "bed<TAB>temp_c" (whole degrees)
#   BED      optional — restrict the report to a single bed
#
# FROST_LIMIT (environment, degrees C) moves the frost line; beds whose
# overnight minimum is strictly below the limit are flagged. Default 0.
# Exit: 0 no frost risk, 1 at least one bed flagged, 2 usage/IO trouble.

if (( $# < 1 )); then
  print -u2 "usage: frostwatch.zsh TEMPLOG [BED]"
  exit 2
fi

log_file=$1
bed_filter=$2

if [[ ! -r $log_file ]]; then
  print -u2 "frostwatch: cannot read $log_file"
  exit 2
fi

typeset -A mins
typeset -a order

load_readings() {
  for line in "${(f)$(<$log_file)}"; do
    bed=${line%%$'\t'*}
    temp=${line##*$'\t'}
    [[ -n $bed ]] || continue
    if (( ! ${+mins[$bed]} )); then
      order+=($bed)
      mins[$bed]=$temp
    elif (( temp < mins[$bed] )); then
      mins[$bed]=$temp
    fi
  done
}

report() {
  limit=${FROST_LIMIT}
  frosty=0
  checked=0
  print "frost report (limit ${limit}C)"
  for bed in $order; do
    if [[ -n $bed_filter && $bed != $bed_filter ]]; then
      continue
    fi
    checked=$((checked + 1))
    if (( mins[$bed] < limit )); then
      print "$bed min ${mins[$bed]} FROST"
      frosty=$((frosty + 1))
    else
      print "$bed min ${mins[$bed]} ok"
    fi
  done
  print "checked $checked bed(s), $frosty frosty"
  (( frosty == 0 ))
}

load_readings
report
exit $?
