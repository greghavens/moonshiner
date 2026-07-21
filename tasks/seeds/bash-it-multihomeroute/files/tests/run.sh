#!/usr/bin/env bash

set -u

project=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
router="$project/bin/multihome-route"
config="$project/etc/multihome.conf"
fixtures="$project/tests/fixtures"
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
failures=0

pass() {
  printf 'ok - %s\n' "$1"
}

fail() {
  printf 'not ok - %s\n' "$1"
  failures=$((failures + 1))
}

assert_same() {
  local name=$1 expected=$2 actual=$3
  if diff -u "$expected" "$actual"; then
    pass "$name"
  else
    fail "$name"
  fi
}

if bash -n "$router"; then
  pass 'router has valid Bash syntax'
else
  fail 'router has valid Bash syntax'
fi

if "$router" plan "$config" > "$scratch/actual.plan"; then
  assert_same 'plan uses local source policies and stable metrics' \
    "$fixtures/expected.plan" "$scratch/actual.plan"
else
  fail 'plan command succeeds'
fi

state_new="$scratch/new-state"
if "$router" apply "$config" "$state_new"; then
  assert_same 'apply stages the complete plan' "$fixtures/expected.plan" "$state_new/active.plan"
  if [[ -e "$state_new/transaction.open" && ! -e "$state_new/rollback.had-active" ]]; then
    pass 'new apply records a reversible transaction'
  else
    fail 'new apply records a reversible transaction'
  fi
  if "$router" rollback "$state_new" && [[ ! -e "$state_new/active.plan" && ! -e "$state_new/transaction.open" ]]; then
    pass 'rollback removes a newly staged plan'
  else
    fail 'rollback removes a newly staged plan'
  fi
else
  fail 'apply stages the complete plan'
fi

state_existing="$scratch/existing-state"
mkdir -p "$state_existing"
cp "$fixtures/previous.plan" "$state_existing/active.plan"
if "$router" apply "$config" "$state_existing" && "$router" rollback "$state_existing"; then
  assert_same 'rollback restores the exact previous plan' \
    "$fixtures/previous.plan" "$state_existing/active.plan"
else
  fail 'rollback restores the exact previous plan'
fi

state_verify="$scratch/verify-state"
if "$router" apply "$config" "$state_verify"; then
  if "$router" verify "$config" "$state_verify" "$fixtures/return-paths.tsv" > "$scratch/verify.out"; then
    cat > "$scratch/verify.expected" <<'EOF'
OK management reply from 192.0.2.10 to 203.0.113.44 returns via mgmt0 (mgmt)
OK application reply from 198.51.100.10 to 203.0.113.77 returns via app0 (app)
EOF
    assert_same 'return-path evidence verifies both traffic classes' \
      "$scratch/verify.expected" "$scratch/verify.out"
  else
    cat "$scratch/verify.out"
    fail 'return-path evidence verifies both traffic classes'
  fi
else
  fail 'verification setup applies'
fi

if ((failures > 0)); then
  printf '%d test(s) failed\n' "$failures" >&2
  exit 1
fi
printf 'all tests passed\n'
