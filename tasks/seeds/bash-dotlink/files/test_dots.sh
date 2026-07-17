#!/usr/bin/env bash
# Acceptance harness for dots.sh.
# Run from the workspace root:  bash test_dots.sh
set -u
LC_ALL=C
export LC_ALL
unset CDPATH

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- "${0%/*}"

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
cleanup() { rm -rf "$ROOT/$T"; }
trap cleanup EXIT

checks=0
fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  checks=$((checks + 1))
  if [[ "$2" == "$3" ]]; then
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
  return 1
}

assert_true() { # assert_true <label> <rc-of-condition>
  checks=$((checks + 1))
  if [[ "$2" -eq 0 ]]; then
    printf 'PASS %s\n' "$1"
    return 0
  fi
  fails=$((fails + 1))
  printf 'FAIL %s\n' "$1"
  return 1
}

slurp() { # slurp <var> <file> -- byte-exact file contents into var
  IFS= read -r -d '' "$1" < "$2" || true
}

RC=0
OUT=''
ERR=''
run_in() { # run_in <dir> <cmd...> -- capture RC, OUT, ERR byte-exactly
  local d=$1
  shift
  ( cd "$d" && exec "$@" ) > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  slurp OUT "$ROOT/$T/out"
  slurp ERR "$ROOT/$T/err"
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

if [[ ! -f dots.sh ]]; then
  printf 'FAIL dots.sh not found in the workspace root\n'
  exit 1
fi

# ---- kit fixture ----------------------------------------------------------------

KIT="$T/kit"
mkdir -p "$KIT/hooks"   # subdirectory in the kit: ignored
printf '[user]\n\tname = Lab\n' > "$KIT/.gitconfig"
printf 'alias ll="ls -l"\n'     > "$KIT/.shellrc"
printf 'set hidden\n'           > "$KIT/.vimrc"
printf '*.swp\n'                > "$KIT/gitignore-global"

SRCABS=$(cd "$KIT" && pwd)

# home fixture: one personalized real file, one foreign link, one bystander
HOME1="$T/home1"
mkdir -p "$HOME1" "$T/elsewhere"
printf 'set number\n' > "$HOME1/.vimrc"
printf 'old gitconfig\n' > "$T/elsewhere/gitconfig-old"
ln -s "../elsewhere/gitconfig-old" "$HOME1/.gitconfig"
printf 'do not touch\n' > "$HOME1/README"

# ---- first install: link, relink, backup+link ------------------------------------

printf -v exp_install1 'relink .gitconfig\nlink .shellrc\nbackup+link .vimrc\nlink gitignore-global\ndone: 4 changed, 0 ok\n'

run_in "$T" bash "$ROOT/dots.sh" install kit home1
expect "first install manifest" 0 "$exp_install1" ""

for n in .gitconfig .shellrc .vimrc gitignore-global; do
  [[ -L "$HOME1/$n" ]]; assert_true "$n is a symlink" "$?"
  assert_eq "$n points at the absolute kit path" "$SRCABS/$n" "$(readlink "$HOME1/$n")"
  cmp -s "$HOME1/$n" "$KIT/$n"; assert_true "$n resolves to kit content" "$?"
done

assert_eq "personalized .vimrc was backed up" 'set number' "$(cat "$HOME1/dotbackup/.vimrc")"
assert_eq "dotbackup holds only the real file" '.vimrc' "$( cd "$HOME1/dotbackup" && ls -A )"
assert_eq "bystander README untouched" 'do not touch' "$(cat "$HOME1/README")"
assert_eq "foreign link's old destination untouched" 'old gitconfig' "$(cat "$T/elsewhere/gitconfig-old")"

# ---- second install: pure no-op ---------------------------------------------------

printf -v exp_install2 'ok .gitconfig\nok .shellrc\nok .vimrc\nok gitignore-global\ndone: 0 changed, 4 ok\n'

run_in "$T" bash "$ROOT/dots.sh" install kit home1
expect "second install is a no-op" 0 "$exp_install2" ""
assert_eq "backup untouched by the no-op" 'set number' "$(cat "$HOME1/dotbackup/.vimrc")"

# ---- uninstall: restore, unlink, skip ---------------------------------------------

rm "$HOME1/.shellrc"   # someone removed one link by hand: uninstall must skip it

printf -v exp_uninstall 'unlink .gitconfig\nskip .shellrc\nrestore .vimrc\nunlink gitignore-global\ndone: 3 changed, 1 skipped\n'

run_in "$T" bash "$ROOT/dots.sh" uninstall kit home1
expect "uninstall manifest" 0 "$exp_uninstall" ""

[[ ! -L "$HOME1/.vimrc" && -f "$HOME1/.vimrc" ]]; assert_true ".vimrc is a real file again" "$?"
assert_eq "restored .vimrc has the personalized bytes" 'set number' "$(cat "$HOME1/.vimrc")"
[[ ! -e "$HOME1/.gitconfig" && ! -L "$HOME1/.gitconfig" ]]; assert_true ".gitconfig link removed" "$?"
[[ ! -e "$HOME1/gitignore-global" && ! -L "$HOME1/gitignore-global" ]]; assert_true "gitignore-global link removed" "$?"
[[ ! -e "$HOME1/dotbackup" ]]; assert_true "empty dotbackup removed" "$?"
assert_eq "bystander README survives uninstall" 'do not touch' "$(cat "$HOME1/README")"

# ---- uninstall on a never-installed home: all skip --------------------------------

HOME2="$T/home2"
mkdir -p "$HOME2"
printf 'mine\n' > "$HOME2/.vimrc"

printf -v exp_skip 'skip .gitconfig\nskip .shellrc\nskip .vimrc\nskip gitignore-global\ndone: 0 changed, 4 skipped\n'
run_in "$T" bash "$ROOT/dots.sh" uninstall kit home2
expect "uninstall without an install skips everything" 0 "$exp_skip" ""
assert_eq "unrelated real .vimrc untouched" 'mine' "$(cat "$HOME2/.vimrc")"

# ---- preflight: occupied backup slot refuses the whole run ------------------------

HOME3="$T/home3"
mkdir -p "$HOME3/dotbackup"
printf 'current words\n' > "$HOME3/.vimrc"
printf 'stale backup\n'  > "$HOME3/dotbackup/.vimrc"

printf -v exp_bakclash 'dots.sh: backup already exists: .vimrc\n'
run_in "$T" bash "$ROOT/dots.sh" install kit home3
expect "occupied backup slot refused" 73 "" "$exp_bakclash"
[[ ! -e "$HOME3/.shellrc" && ! -L "$HOME3/.shellrc" ]]; assert_true "refused install links nothing" "$?"
assert_eq "real file untouched by refused install" 'current words' "$(cat "$HOME3/.vimrc")"
assert_eq "stale backup untouched by refused install" 'stale backup' "$(cat "$HOME3/dotbackup/.vimrc")"

# ---- preflight: directory sitting at a target path --------------------------------

HOME4="$T/home4"
mkdir -p "$HOME4/.shellrc"

printf -v exp_dirclash 'dots.sh: target is a directory: .shellrc\n'
run_in "$T" bash "$ROOT/dots.sh" install kit home4
expect "directory target refused" 73 "" "$exp_dirclash"
[[ ! -e "$HOME4/.gitconfig" && ! -L "$HOME4/.gitconfig" ]]; assert_true "refused install creates no links" "$?"

# ---- invocation errors -------------------------------------------------------------

printf -v exp_usage 'usage: dots.sh install|uninstall <srcdir> <targetdir>\n'

run_in "$T" bash "$ROOT/dots.sh"
expect "no arguments" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dots.sh" sync kit home1
expect "unknown subcommand" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dots.sh" install kit
expect "missing targetdir" 64 "" "$exp_usage"

run_in "$T" bash "$ROOT/dots.sh" install kit home1 extra
expect "extra argument" 64 "" "$exp_usage"

printf -v exp_nosrc 'dots.sh: not a directory: nokit\n'
run_in "$T" bash "$ROOT/dots.sh" install nokit home1
expect "missing srcdir" 66 "" "$exp_nosrc"

printf -v exp_notgt 'dots.sh: not a directory: nohome\n'
run_in "$T" bash "$ROOT/dots.sh" install kit nohome
expect "missing targetdir dir" 66 "" "$exp_notgt"

EMPTYKIT="$T/emptykit"
mkdir -p "$EMPTYKIT/subdir-only"
printf -v exp_emptykit 'dots.sh: nothing to manage in: emptykit\n'
run_in "$T" bash "$ROOT/dots.sh" install emptykit home2
expect "kit without regular files" 65 "" "$exp_emptykit"

# ---- summary -------------------------------------------------------------------

if [[ "$fails" -gt 0 ]]; then
  printf 'SUMMARY: %d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'SUMMARY: all %d checks passed\n' "$checks"
