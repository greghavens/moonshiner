#!/usr/bin/env bash
set -euo pipefail

TEST_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_DIR=$(cd -- "$TEST_DIR/.." && pwd -P)
TOOL=$REPO_DIR/bin/linuxperms
TMP_CASE=$(mktemp -d "${TMPDIR:-/tmp}/linuxperms-tests.XXXXXX")
trap 'rm -rf -- "$TMP_CASE"' EXIT HUP INT TERM

fail() {
  printf 'not ok - %s\n' "$*" >&2
  exit 1
}

assert_file() {
  [[ -f $1 ]] || fail "expected file: $1"
}

assert_same() {
  cmp -s -- "$1" "$2" || {
    diff -u -- "$1" "$2" >&2 || true
    fail "files differ: $1 $2"
  }
}

make_main_fixture() {
  local base=$1 root=$base/root
  mkdir -p "$root/shared/team/docs/archive" "$root/notes"
  printf 'welcome\n' > "$root/shared/README.txt"
  printf 'specification v1\n' > "$root/shared/team/docs/spec.txt"
  printf 'old build\n' > "$root/shared/team/docs/archive/build.log"
  printf 'do not alter me\n' > "$root/notes/keep.txt"
  ln -s docs/spec.txt "$root/shared/team/latest"

  cat > "$base/state.tsv" <<'EOF'
path	type	uid	gid	mode	access_acl	default_acl
shared	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-
shared/README.txt	file	41	42	0644	u::rw-,g::r--,o::r--	-
shared/team	dir	41	42	0750	u::rwx,g::r-x,o::---	u::rwx,g::r-x,o::---
shared/team/docs	dir	51	52	0700	u::rwx,g::---,o::---	-
shared/team/docs/spec.txt	file	51	52	0600	u::rw-,g::---,o::---	-
shared/team/docs/archive	dir	61	62	0777	u::rwx,g::rwx,o::rwx	u::rwx,g::rwx,o::rwx
shared/team/docs/archive/build.log	file	61	62	0666	u::rw-,g::rw-,o::rw-	-
notes/keep.txt	file	9000	9001	0640	u::rw-,g::r--,o::---	-
EOF
  cp -- "$base/state.tsv" "$base/state.before.tsv"

  cat > "$base/identities.tsv" <<'EOF'
name	uid	groups
builder	1201	3300,4401
auditor	1202	3300,4402
EOF

  cat > "$base/matrix.tsv" <<'EOF'
path	name	permissions
shared	auditor	r-x
shared	builder	rwx
shared/README.txt	auditor	r--
shared/README.txt	builder	rw-
shared/team	auditor	r-x
shared/team	builder	rwx
shared/team/docs	auditor	r-x
shared/team/docs	builder	rwx
shared/team/docs/archive	auditor	r-x
shared/team/docs/archive	builder	rwx
shared/team/docs/archive/build.log	auditor	r--
shared/team/docs/archive/build.log	builder	rw-
shared/team/docs/spec.txt	auditor	r--
shared/team/docs/spec.txt	builder	rw-
EOF
}

test_recursive_reconcile_matrix_and_rollback() {
  local base=$TMP_CASE/main root expected_state expected_manifest before_content after_content state_sum
  mkdir -p "$base"
  make_main_fixture "$base"
  root=$base/root

  before_content=$(find -P "$root" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)
  "$TOOL" apply --root "$root" --state "$base/state.tsv" --rollback "$base/rollback.tsv"

  expected_state=$base/expected-state.tsv
  cat > "$expected_state" <<'EOF'
path	type	uid	gid	mode	access_acl	default_acl
shared	dir	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/README.txt	file	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
shared/team	dir	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs	dir	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs/spec.txt	file	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
shared/team/docs/archive	dir	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs/archive/build.log	file	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
notes/keep.txt	file	9000	9001	0640	u::rw-,g::r--,o::---	-
EOF
  assert_same "$expected_state" "$base/state.tsv"

  expected_manifest=$base/expected-rollback.tsv
  cat > "$expected_manifest" <<'EOF'
linuxperms-rollback-v1
path	type	previous_uid	previous_gid	previous_mode	previous_access_acl	previous_default_acl	applied_uid	applied_gid	applied_mode	applied_access_acl	applied_default_acl
shared	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/README.txt	file	41	42	0644	u::rw-,g::r--,o::r--	-	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
shared/team	dir	41	42	0750	u::rwx,g::r-x,o::---	u::rwx,g::r-x,o::---	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs	dir	51	52	0700	u::rwx,g::---,o::---	-	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs/archive	dir	61	62	0777	u::rwx,g::rwx,o::rwx	u::rwx,g::rwx,o::rwx	2200	3300	2770	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---	u::rwx,u:1201:rwx,u:1202:r-x,g::rwx,m::rwx,o::---
shared/team/docs/archive/build.log	file	61	62	0666	u::rw-,g::rw-,o::rw-	-	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
shared/team/docs/spec.txt	file	51	52	0600	u::rw-,g::---,o::---	-	2200	3300	0660	u::rw-,u:1201:rw-,u:1202:r--,g::rw-,m::rw-,o::---	-
EOF
  assert_same "$expected_manifest" "$base/rollback.tsv"

  after_content=$(find -P "$root" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)
  [[ $before_content == "$after_content" ]] || fail 'managed or unrelated file content changed'
  [[ $(readlink -- "$root/shared/team/latest") == docs/spec.txt ]] || fail 'internal symlink changed'
  grep -Fxq $'notes/keep.txt\tfile\t9000\t9001\t0640\tu::rw-,g::r--,o::---\t-' "$base/state.tsv" ||
    fail 'unrelated metadata changed'

  "$TOOL" verify --root "$root" --state "$base/state.tsv" \
    --identities "$base/identities.tsv" --matrix "$base/matrix.tsv"

  cp -- "$base/matrix.tsv" "$base/bad-matrix.tsv"
  sed -i $'s/shared\\tauditor\\tr-x/shared\\tauditor\\trwx/' "$base/bad-matrix.tsv"
  if "$TOOL" verify --root "$root" --state "$base/state.tsv" \
      --identities "$base/identities.tsv" --matrix "$base/bad-matrix.tsv" >/dev/null 2>&1; then
    fail 'verify accepted an incorrect second-user access matrix'
  fi

  state_sum=$(sha256sum "$base/state.tsv")
  "$TOOL" apply --root "$root" --state "$base/state.tsv" --rollback "$base/noop-rollback.tsv"
  [[ $(sha256sum "$base/state.tsv") == "$state_sum" ]] || fail 'idempotent apply rewrote state content'
  [[ $(wc -l < "$base/noop-rollback.tsv") -eq 2 ]] || fail 'no-op rollback manifest contains changes'

  "$TOOL" rollback --state "$base/state.tsv" --manifest "$base/rollback.tsv"
  assert_same "$base/state.before.tsv" "$base/state.tsv"
  printf 'ok - recursive reconciliation, matrices, preservation, and rollback\n'
}

test_symlink_escape_is_transactional() {
  local base=$TMP_CASE/escape root before outside_before
  mkdir -p "$base/root/shared/team" "$base/outside"
  root=$base/root
  printf 'external secret\n' > "$base/outside/secret.txt"
  printf 'inside\n' > "$root/shared/team/data.txt"
  ln -s ../../../outside/secret.txt "$root/shared/team/escape"
  cat > "$base/state.tsv" <<'EOF'
path	type	uid	gid	mode	access_acl	default_acl
shared	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-
shared/team	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-
shared/team/data.txt	file	0	0	0644	u::rw-,g::r--,o::r--	-
EOF
  before=$(sha256sum "$base/state.tsv")
  outside_before=$(sha256sum "$base/outside/secret.txt")
  if "$TOOL" apply --root "$root" --state "$base/state.tsv" --rollback "$base/must-not-exist.tsv" \
      >"$base/stdout" 2>"$base/stderr"; then
    fail 'escaping symlink was accepted'
  fi
  grep -Fq 'symlink escapes managed shared tree' "$base/stderr" || fail 'escape rejection was not explicit'
  [[ $(sha256sum "$base/state.tsv") == "$before" ]] || fail 'escape rejection changed state'
  [[ $(sha256sum "$base/outside/secret.txt") == "$outside_before" ]] || fail 'escape target changed'
  [[ ! -e $base/must-not-exist.tsv ]] || fail 'escape rejection published a rollback manifest'
  printf 'ok - symlink escape rejection is transactional\n'
}

test_dangling_symlink_escape_is_transactional() {
  local base=$TMP_CASE/dangling-escape root before
  mkdir -p "$base/root/shared/team"
  root=$base/root
  printf 'inside\n' > "$root/shared/team/data.txt"
  ln -s ../../../outside/missing.txt "$root/shared/team/escape"
  cat > "$base/state.tsv" <<'EOF'
path	type	uid	gid	mode	access_acl	default_acl
shared	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-
shared/team	dir	0	0	0755	u::rwx,g::r-x,o::r-x	-
shared/team/data.txt	file	0	0	0644	u::rw-,g::r--,o::r--	-
EOF
  before=$(sha256sum "$base/state.tsv")
  if "$TOOL" apply --root "$root" --state "$base/state.tsv" --rollback "$base/must-not-exist.tsv" \
      >"$base/stdout" 2>"$base/stderr"; then
    fail 'dangling escaping symlink was accepted'
  fi
  grep -Fq 'symlink escapes managed shared tree' "$base/stderr" ||
    fail 'dangling escape rejection was not explicit'
  [[ $(sha256sum "$base/state.tsv") == "$before" ]] || fail 'dangling escape rejection changed state'
  [[ $(readlink -- "$root/shared/team/escape") == ../../../outside/missing.txt ]] ||
    fail 'dangling escaping symlink changed'
  [[ ! -e $base/must-not-exist.tsv ]] || fail 'dangling escape rejection published a rollback manifest'
  printf 'ok - dangling symlink escape rejection is transactional\n'
}

[[ -x $TOOL ]] || fail 'bin/linuxperms is not executable'
test_recursive_reconcile_matrix_and_rollback
test_symlink_escape_is_transactional
test_dangling_symlink_escape_is_transactional
printf 'all tests passed\n'
