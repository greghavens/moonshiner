#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
audit=$project_dir/package_audit.sh
fixtures=$project_dir/fixtures
tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

cat > "$tmp_dir/expected-all.txt" <<'EXPECTED'
AUDIT
HOLD dashboard 1.4.2 -> 1.5.0: requires engine >= 2.0.0, projected 1.9.4
HOLD engine 1.9.4 -> 2.0.0: pin requires < 2.0.0 (legacy dashboard plugin requires engine 1.x)
UPGRADE logger 3.1.0 -> 3.2.1
PLAN
PLAN logger 3.1.0 -> 3.2.1
SIMULATION
WOULD_APPLY logger 3.1.0 -> 3.2.1
VERIFICATION
VERIFIED 1 planned upgrade(s); no unauthorized major-version changes
EXPECTED

"$audit" all "$fixtures" > "$tmp_dir/actual-all.txt"
if ! diff -u --label expected-all.txt --label actual-all.txt \
    "$tmp_dir/expected-all.txt" "$tmp_dir/actual-all.txt"; then
  fail 'the audit, plan, simulation, or verification is incorrect'
fi
printf 'ok - exclusive pin produces a bounded, dependency-safe plan\n'

mkdir "$tmp_dir/edge"
cat > "$tmp_dir/edge/installed.tsv" <<'EOF_INSTALLED'
boundary	9.4.0
EOF_INSTALLED
cat > "$tmp_dir/edge/repository.tsv" <<'EOF_REPOSITORY'
boundary	10.0.0
EOF_REPOSITORY
cat > "$tmp_dir/edge/pins.tsv" <<'EOF_PINS'
boundary	<	10.0.0	major 10 has not been approved
EOF_PINS
: > "$tmp_dir/edge/dependencies.tsv"

expected_edge='HOLD boundary 9.4.0 -> 10.0.0: pin requires < 10.0.0 (major 10 has not been approved)'
actual_edge=$("$audit" audit "$tmp_dir/edge")
[[ $actual_edge == "$expected_edge" ]] || fail 'a multi-digit exclusive pin ceiling accepted equality'
printf 'ok - multi-digit major versions honor the same exclusive boundary\n'

mkdir "$tmp_dir/below"
cat > "$tmp_dir/below/installed.tsv" <<'EOF_INSTALLED'
worker	10.4.0
EOF_INSTALLED
cat > "$tmp_dir/below/repository.tsv" <<'EOF_REPOSITORY'
worker	10.9.0
EOF_REPOSITORY
cat > "$tmp_dir/below/pins.tsv" <<'EOF_PINS'
worker	<	11.0.0	major 11 has not been approved
EOF_PINS
: > "$tmp_dir/below/dependencies.tsv"

expected_below='UPGRADE worker 10.4.0 -> 10.9.0'
actual_below=$("$audit" audit "$tmp_dir/below")
[[ $actual_below == "$expected_below" ]] || fail 'an exclusive pin rejected a version below its ceiling'
printf 'ok - exclusive pins still allow versions below their ceiling\n'

printf '3 tests passed\n'
