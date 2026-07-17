#!/usr/bin/env bash
# rollout.sh — run the deploy steps in order, mirror each step's output to
# logs/<step>.log for ops to tail, and print a per-step summary at the end.
set -u

steps=(build migrate smoke)

mkdir -p logs
overall=0
summary=()

for s in "${steps[@]}"; do
  echo "rollout: running $s"
  bash "steps/$s.sh" 2>&1 | tee -a "logs/$s.log"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    summary+=("$s: OK")
  else
    summary+=("$s: FAIL (exit $rc)")
    overall=1
  fi
done

echo '==== rollout summary ===='
printf '%s\n' "${summary[@]}"
exit "$overall"
