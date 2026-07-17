#!/usr/bin/env zsh
# Acceptance harness for backup.zsh.
# Run from the workspace root:  zsh test_backup.zsh
#
# Layout of this file:
#   EXISTING BEHAVIOR — what the script already does today; these checks are
#                       green against the shipped backup.zsh and MUST stay
#                       green after the feature work.
#   INCLUDE / EXCLUDE — new selection flags (the feature under review).
#   DRY-RUN           — new plan-only mode (the feature under review).
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL
unset CDPATH cdpath 2>/dev/null

# run from the harness's own directory even if invoked from elsewhere
[[ $0 == */* ]] && cd -- ${0:h}

zmodload zsh/mapfile

ROOT=$PWD
T=_t
rm -rf "$T"
mkdir -p "$T"
trap 'rm -rf "$ROOT/$T"' EXIT

TAB=$'\t'
typeset -i checks=0 fails=0

assert_eq() { # assert_eq <label> <expected> <actual>
  (( checks += 1 ))
  if [[ "$2" == "$3" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- expected ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_like() { # assert_like <label> <pattern> <actual>
  (( checks += 1 ))
  if [[ "$3" == ${~2} ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s\n--- pattern ---\n%s\n--- actual ---\n%s\n----------------\n' "$1" "$2" "$3"
}

assert_absent() { # assert_absent <label> <path>
  (( checks += 1 ))
  if [[ ! -e "$2" ]]; then
    return 0
  fi
  (( fails += 1 ))
  printf 'FAIL %s: %s exists but must not\n' "$1" "$2"
}

mkexp() { # mkexp <var> <line>... -- join lines with \n plus trailing newline
  local __n=$1
  shift
  : ${(P)__n::=${(F)@}$'\n'}
}

RC=0
OUT=''
ERR=''
run() { # run <cmd...> -- capture RC, OUT, ERR byte-exactly
  "$@" > "$ROOT/$T/out" 2> "$ROOT/$T/err"
  RC=$?
  OUT=${mapfile[$ROOT/$T/out]-}
  ERR=${mapfile[$ROOT/$T/err]-}
}

expect() { # expect <label> <rc> <expected-stdout> <expected-stderr>
  assert_eq "$1: exit code" "$2" "$RC"
  assert_eq "$1: stdout" "$3" "$OUT"
  assert_eq "$1: stderr" "$4" "$ERR"
}

list_files() { # list_files <dir> -- every file under dir (dotfiles too), rel paths, byte order
  local d=$1
  local -a fs
  fs=( $d/**/*(.DNon) )
  fs=( "${(@)fs#$d/}" )
  (( ${#fs} > 0 )) && print -rl -- "${fs[@]}"
  return 0
}

snapshot() { # snapshot <dir> -- "relpath cksum bytes" per file, dotfiles included
  local d=$1 f
  local -a fs
  fs=( $d/**/*(.DNon) )
  for f in "${fs[@]}"; do
    print -r -- "${f#$d/} $(cksum < "$f")"
  done
  return 0
}

if [[ ! -f backup.zsh ]]; then
  print -r -- 'FAIL backup.zsh not found in the workspace root'
  exit 1
fi

# ---- fixture tree -----------------------------------------------------------------

SRC=$T/src
mkdir -p "$SRC/logs" "$SRC/cache/deep" "$SRC/docs/img" "$SRC/.cache"
print -r -- 'retention_days=30'                       > "$SRC/app.conf"
print -r -- 'remember the offsite drill'              > "$SRC/notes.txt"
print -r -- 'second notebook, still paper'            > "$SRC/notes 2.txt"
print -r -- '- rotate the drop disks'                 > "$SRC/todo.md"
print -r -- '2026-06-30 rotation ok'                  > "$SRC/logs/june.log"
print -r -- '2026-07-31 rotation ok'                  > "$SRC/logs/july.log"
print -r -- 'scratch 1'                               > "$SRC/cache/tmp1.dat"
print -r -- 'scratch 2'                               > "$SRC/cache/deep/tmp2.dat"
printf '%s\n%s\n' '# restore guide' 'step one: stay calm' > "$SRC/docs/guide.md"
print -r -- '<svg><!-- not really --></svg>'          > "$SRC/docs/img/logo.svg"
print -r -- 'editor droppings'                        > "$SRC/.notes.swp"
print -r -- 'hidden blob'                             > "$SRC/.cache/blob.bin"

# =====================================================================================
# EXISTING BEHAVIOR — green against the shipped backup.zsh; must stay green
# =====================================================================================

# ---- full copy: every visible plain file, manifest in byte order -------------------

run zsh backup.zsh "$SRC" "$T/d1"
expect 'full copy' 0 $'files copied: 10\n' ''

mkexp exp_list \
  'app.conf' \
  'cache/deep/tmp2.dat' \
  'cache/tmp1.dat' \
  'docs/guide.md' \
  'docs/img/logo.svg' \
  'logs/july.log' \
  'logs/june.log' \
  'manifest.txt' \
  'notes 2.txt' \
  'notes.txt' \
  'todo.md'
run list_files "$T/d1"
expect 'full copy: destination holds exactly the visible files' 0 "$exp_list" ''

mkexp exp_man \
  'app.conf' \
  'cache/deep/tmp2.dat' \
  'cache/tmp1.dat' \
  'docs/guide.md' \
  'docs/img/logo.svg' \
  'logs/july.log' \
  'logs/june.log' \
  'notes 2.txt' \
  'notes.txt' \
  'todo.md'
man_full=$exp_man
assert_eq 'full copy: manifest bytes' "$exp_man" "${mapfile[$T/d1/manifest.txt]-}"

mkexp exp_g '# restore guide' 'step one: stay calm'
assert_eq 'full copy: nested file content intact' "$exp_g" "${mapfile[$T/d1/docs/guide.md]-}"
assert_eq 'full copy: space in a name survives' $'second notebook, still paper\n' "${mapfile[$T/d1/notes 2.txt]-}"

# ---- running the same drop again is uneventful --------------------------------------

run zsh backup.zsh "$SRC" "$T/d1"
expect 'second run over the same destination' 0 $'files copied: 10\n' ''
assert_eq 'second run: manifest unchanged' "$man_full" "${mapfile[$T/d1/manifest.txt]-}"

# ---- empty source: destination and manifest still created ---------------------------

mkdir -p "$T/srcempty"
run zsh backup.zsh "$T/srcempty" "$T/d3"
expect 'empty source' 0 $'files copied: 0\n' ''
mkexp exp_list 'manifest.txt'
run list_files "$T/d3"
expect 'empty source: destination holds only the manifest' 0 "$exp_list" ''
assert_eq 'empty source: manifest is empty' '' "${mapfile[$T/d3/manifest.txt]-}"

# ---- argument errors ------------------------------------------------------------------

run zsh backup.zsh
assert_eq 'no arguments: exit code' 64 "$RC"
assert_eq 'no arguments: stdout' '' "$OUT"
assert_like 'no arguments: stderr' 'usage: backup.zsh *' "$ERR"

run zsh backup.zsh "$SRC"
assert_eq 'one argument: exit code' 64 "$RC"
assert_eq 'one argument: stdout' '' "$OUT"
assert_like 'one argument: stderr' 'usage: backup.zsh *' "$ERR"

run zsh backup.zsh "$SRC" "$T/dx" extra
assert_eq 'three positionals: exit code' 64 "$RC"
assert_eq 'three positionals: stdout' '' "$OUT"
assert_like 'three positionals: stderr' 'usage: backup.zsh *' "$ERR"

# ---- source must be a directory ---------------------------------------------------------

mkexp exp_err "backup.zsh: not a directory: $SRC/app.conf"
run zsh backup.zsh "$SRC/app.conf" "$T/never"
expect 'source is a file' 66 '' "$exp_err"
assert_absent 'source is a file: nothing created' "$T/never"

mkexp exp_err "backup.zsh: not a directory: $T/nosuch"
run zsh backup.zsh "$T/nosuch" "$T/never2"
expect 'source is missing' 66 '' "$exp_err"

# =====================================================================================
# INCLUDE / EXCLUDE — the feature under review
#
# Patterns are zsh extended globs matched against each file's RELATIVE path as
# one string (${~pat} under extended_glob). In that matching context * crosses
# directory boundaries: '*.log' catches logs at any depth, 'cache/*' covers the
# whole subtree. Excludes always beat includes.
# =====================================================================================

new_usage='usage: backup.zsh [--include <pat>]... [--exclude <pat>]... [--dry-run] <src-dir> <dest-dir>'

# ---- include: '*.md' selects md files at every depth --------------------------------

run zsh backup.zsh --include '*.md' "$SRC" "$T/f1"
expect 'include *.md' 0 $'files copied: 2\n' ''
mkexp exp_man 'docs/guide.md' 'todo.md'
assert_eq 'include *.md: manifest' "$exp_man" "${mapfile[$T/f1/manifest.txt]-}"
mkexp exp_list 'docs/guide.md' 'manifest.txt' 'todo.md'
run list_files "$T/f1"
expect 'include *.md: nothing else copied' 0 "$exp_list" ''

# ---- include: 'docs/*' covers the whole docs subtree (star crosses /) -----------------

run zsh backup.zsh --include 'docs/*' "$SRC" "$T/f2"
expect 'include docs/*' 0 $'files copied: 2\n' ''
mkexp exp_man 'docs/guide.md' 'docs/img/logo.svg'
assert_eq 'include docs/*: manifest' "$exp_man" "${mapfile[$T/f2/manifest.txt]-}"

# ---- include: pattern matching a name with a space -------------------------------------

run zsh backup.zsh --include 'notes*' "$SRC" "$T/f2b"
expect 'include notes*' 0 $'files copied: 2\n' ''
mkexp exp_man 'notes 2.txt' 'notes.txt'
assert_eq 'include notes*: manifest' "$exp_man" "${mapfile[$T/f2b/manifest.txt]-}"

# ---- excludes: log noise and the cache subtree stay home --------------------------------

run zsh backup.zsh --exclude '*.log' --exclude 'cache/*' "$SRC" "$T/f3"
expect 'two excludes' 0 $'files copied: 6\n' ''
mkexp exp_man \
  'app.conf' \
  'docs/guide.md' \
  'docs/img/logo.svg' \
  'notes 2.txt' \
  'notes.txt' \
  'todo.md'
man_f3=$exp_man
assert_eq 'two excludes: manifest' "$exp_man" "${mapfile[$T/f3/manifest.txt]-}"
mkexp exp_list \
  'app.conf' \
  'docs/guide.md' \
  'docs/img/logo.svg' \
  'manifest.txt' \
  'notes 2.txt' \
  'notes.txt' \
  'todo.md'
run list_files "$T/f3"
expect 'two excludes: destination contents' 0 "$exp_list" ''

# ---- precedence: an exclude removes a file an include selected ---------------------------

run zsh backup.zsh --include '*.md' --exclude 'docs/*' "$SRC" "$T/f4"
expect 'exclude beats include' 0 $'files copied: 1\n' ''
mkexp exp_man 'todo.md'
assert_eq 'exclude beats include: manifest' "$exp_man" "${mapfile[$T/f4/manifest.txt]-}"
mkexp exp_list 'manifest.txt' 'todo.md'
run list_files "$T/f4"
expect 'exclude beats include: destination contents' 0 "$exp_list" ''

# ---- case-insensitive pattern flag --------------------------------------------------------

run zsh backup.zsh --include '(#i)*.MD' "$SRC" "$T/f5"
expect 'case-insensitive include' 0 $'files copied: 2\n' ''
mkexp exp_man 'docs/guide.md' 'todo.md'
assert_eq 'case-insensitive include: manifest' "$exp_man" "${mapfile[$T/f5/manifest.txt]-}"

# ---- include that matches nothing: real run still writes an empty drop ---------------------

run zsh backup.zsh --include 'zzz*' "$SRC" "$T/f6"
expect 'include with no takers' 0 $'files copied: 0\n' ''
assert_eq 'include with no takers: manifest is empty' '' "${mapfile[$T/f6/manifest.txt]-}"
mkexp exp_list 'manifest.txt'
run list_files "$T/f6"
expect 'include with no takers: destination holds only the manifest' 0 "$exp_list" ''

# ---- flag errors ----------------------------------------------------------------------------

mkexp exp_err "$new_usage"
run zsh backup.zsh --include
expect 'include without a value' 64 '' "$exp_err"

run zsh backup.zsh --wat "$SRC" "$T/f7"
expect 'unknown flag' 64 '' "$exp_err"

# =====================================================================================
# DRY-RUN — the feature under review
#
# Plan only: one 'copy<TAB><relative path>' line per selected file, byte order,
# exit 0, and the filesystem is not touched — no destination directory, no
# writes anywhere. No summary line in this mode.
# =====================================================================================

snap_before=$(snapshot "$SRC")

# ---- full plan --------------------------------------------------------------------------

mkexp exp_out \
  "copy${TAB}app.conf" \
  "copy${TAB}cache/deep/tmp2.dat" \
  "copy${TAB}cache/tmp1.dat" \
  "copy${TAB}docs/guide.md" \
  "copy${TAB}docs/img/logo.svg" \
  "copy${TAB}logs/july.log" \
  "copy${TAB}logs/june.log" \
  "copy${TAB}notes 2.txt" \
  "copy${TAB}notes.txt" \
  "copy${TAB}todo.md"
run zsh backup.zsh --dry-run "$SRC" "$T/dd1"
expect 'dry-run: full plan' 0 "$exp_out" ''
assert_absent 'dry-run: destination never created' "$T/dd1"
assert_eq 'dry-run: source tree untouched' "$snap_before" "$(snapshot "$SRC")"

# ---- filtered plan ------------------------------------------------------------------------

mkexp exp_out \
  "copy${TAB}app.conf" \
  "copy${TAB}docs/guide.md" \
  "copy${TAB}docs/img/logo.svg" \
  "copy${TAB}notes 2.txt" \
  "copy${TAB}notes.txt" \
  "copy${TAB}todo.md"
run zsh backup.zsh --dry-run --exclude '*.log' --exclude 'cache/*' "$SRC" "$T/dd2"
expect 'dry-run: filtered plan' 0 "$exp_out" ''
assert_absent 'dry-run filtered: destination never created' "$T/dd2"
assert_eq 'dry-run filtered: source tree untouched' "$snap_before" "$(snapshot "$SRC")"

# the plan names exactly what the real run with the same filters put in its manifest
stripped=${(F)${(@)${(f)OUT}#copy$'\t'}}$'\n'
assert_eq 'dry-run plan matches the real manifest' "$man_f3" "$stripped"

# ---- empty plan -----------------------------------------------------------------------------

run zsh backup.zsh --dry-run --include 'zzz*' "$SRC" "$T/dd3"
expect 'dry-run: empty plan is empty output' 0 '' ''
assert_absent 'dry-run empty: destination never created' "$T/dd3"

# ---- summary --------------------------------------------------------------------------------

if (( fails > 0 )); then
  printf '%d of %d checks failed\n' "$fails" "$checks"
  exit 1
fi
printf 'all checks passed (%d checks)\n' "$checks"
