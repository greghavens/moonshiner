#!/usr/bin/env bash
set -u

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
routecause="$root_dir/bin/routecause"
fixtures="$root_dir/fixtures"
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/routecause-test.XXXXXX")
trap 'rm -rf "$test_dir"' EXIT
cp "$fixtures/routes.tsv" "$test_dir/routes.fixture.original"

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local output=$1
  local expected=$2
  local label=$3
  [[ $output == *"$expected"* ]] || fail "$label (missing: $expected)"
}

[[ -x $routecause ]] || fail 'bin/routecause must be executable'

set +e
diagnosis=$($routecause diagnose "$fixtures" 2>&1)
diagnosis_status=$?
set -e
[[ $diagnosis_status -eq 1 ]] || fail 'the original fixture must be diagnosed as unreachable'
assert_contains "$diagnosis" \
  'FAIL outbound table=200 route=0.0.0.0/0 via=203.0.113.1 dev=wan1 cause=gateway-not-on-link' \
  'diagnosis must identify the unreachable policy-table gateway'
assert_contains "$diagnosis" \
  'PASS return table=254 route=10.40.0.0/16 via=10.0.0.2 dev=lan0' \
  'diagnosis must verify the return path independently'

plan=$($routecause plan "$fixtures") || fail 'route planning failed'
[[ $plan == 'ADD table=200 prefix=203.0.113.0/24 via=- dev=wan1 metric=0' ]] || \
  fail "wrong repair selected: $plan"

mkdir "$test_dir/simulation"
simulation=$($routecause simulate "$fixtures" "$test_dir/simulation") || \
  fail 'the simulated repair did not establish round-trip reachability'
assert_contains "$simulation" \
  'PASS outbound table=200 route=0.0.0.0/0 via=203.0.113.1 dev=wan1' \
  'outbound path did not use the repaired policy table'
assert_contains "$simulation" \
  'PASS return table=254 route=10.40.0.0/16 via=10.0.0.2 dev=lan0' \
  'return path was not preserved'

simulated="$test_dir/simulation/routes.simulated.tsv"
[[ -f $simulated ]] || fail 'simulation did not create a route-state file'

awk -F '\t' '!/^#/ && $1 != 200' "$fixtures/routes.tsv" > "$test_dir/unrelated.before"
awk -F '\t' '!/^#/ && $1 != 200' "$simulated" > "$test_dir/unrelated.after"
cmp -s "$test_dir/unrelated.before" "$test_dir/unrelated.after" || \
  fail 'an unrelated routing table changed'

route_count=$(awk -F '\t' '
  !/^#/ && $1 == 200 && $2 == "203.0.113.0/24" && $3 == "-" && $4 == "wan1" { count++ }
  END { print count + 0 }
' "$simulated")
[[ $route_count -eq 1 ]] || fail 'the connected route was not added exactly once to table 200'

cp "$simulated" "$test_dir/before-second-apply.tsv"
$routecause apply "$fixtures" "$simulated" || fail 'second apply failed'
cmp -s "$test_dir/before-second-apply.tsv" "$simulated" || fail 'apply is not idempotent'
cmp -s "$test_dir/routes.fixture.original" "$fixtures/routes.tsv" || fail 'source fixtures changed'

# Renumber the policy table in a private fixture copy so a hard-coded table 200
# repair cannot satisfy the policy-selected-table contract.
mkdir "$test_dir/renumbered" "$test_dir/renumbered-simulation"
cp "$fixtures"/*.tsv "$test_dir/renumbered/"
awk -F '\t' 'BEGIN { OFS = "\t" } !/^#/ && $1 == 200 { $1 = 201 } { print }' \
  "$fixtures/routes.tsv" > "$test_dir/renumbered/routes.tsv"
awk -F '\t' 'BEGIN { OFS = "\t" } !/^#/ && $5 == 200 { $5 = 201 } { print }' \
  "$fixtures/rules.tsv" > "$test_dir/renumbered/rules.tsv"

renumbered_plan=$($routecause plan "$test_dir/renumbered") || \
  fail 'route planning failed when the policy table was renumbered'
[[ $renumbered_plan == 'ADD table=201 prefix=203.0.113.0/24 via=- dev=wan1 metric=0' ]] || \
  fail "repair table was hard-coded instead of policy-selected: $renumbered_plan"

renumbered_simulation=$($routecause simulate \
  "$test_dir/renumbered" "$test_dir/renumbered-simulation") || \
  fail 'renumbered policy-table simulation did not establish reachability'
assert_contains "$renumbered_simulation" \
  'PASS outbound table=201 route=0.0.0.0/0 via=203.0.113.1 dev=wan1' \
  'renumbered outbound path did not use its selected policy table'

printf 'ok - policy route repaired without collateral table changes\n'
