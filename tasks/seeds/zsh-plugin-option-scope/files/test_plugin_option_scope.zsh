#!/usr/bin/env zsh
emulate -R zsh
setopt no_unset
LC_ALL=C
export LC_ALL

typeset -i checks=0 fails=0
pass() { print -r -- "ok: $1" }
fail() { (( fails += 1 )); print -r -- "FAIL: $1" }
check() {
  (( checks += 1 ))
  if eval "$1"; then pass "$2"; else fail "$2"; fi
}
eq() {
  (( checks += 1 ))
  if [[ $1 == $2 ]]; then
    pass "$3"
  else
    fail "$3 (got ${(qqq)1}, want ${(qqq)2})"
  fi
}

script_dir=${0:A:h}
cd "$script_dir" || exit 1
rm -rf fixture_root
mkdir -p fixture_root/alpha fixture_root/beta fixture_root/archive-2025
touch fixture_root/README.txt
trap 'rm -rf "$script_dir/fixture_root"' EXIT

# The caller deliberately chooses the opposite of the scan's temporary needs.
unsetopt extendedglob nullglob
setopt nomatch
typeset options_before="${options[extendedglob]}:${options[nullglob]}:${options[nomatch]}"

source ./plugin-loader.zsh
typeset options_after_load="${options[extendedglob]}:${options[nullglob]}:${options[nomatch]}"
eq "$options_after_load" "$options_before" "loading the plugin preserves caller options"
check '(( ${precmd_functions[(I)workspace_status_precmd]} > 0 ))' \
  "precmd hook remains registered"
check '(( ${fpath[(I)${script_dir}/functions]} > 0 ))' \
  "loader keeps the autoload directory on fpath"

workspace_roots "$script_dir/fixture_root"
eq "${(j:,:)${reply:t}}" "alpha,beta" \
  "autoloaded scan uses exclusion and directory qualifiers"
typeset options_after_direct="${options[extendedglob]}:${options[nullglob]}:${options[nomatch]}"
eq "$options_after_direct" "$options_before" \
  "direct autoloaded call restores caller options"

# Reset explicitly so the independent hook evidence remains visible even on
# the shipped failing implementation.
unsetopt extendedglob nullglob
setopt nomatch
WORKSPACE_SCAN_ROOT="$script_dir/fixture_root"
workspace_status_precmd
eq "$WORKSPACE_PLUGIN_COUNT" "2" "hook publishes the project count"
eq "$WORKSPACE_PLUGIN_NAMES" "alpha,beta" "hook publishes ordered names"
typeset options_after_hook="${options[extendedglob]}:${options[nullglob]}:${options[nomatch]}"
eq "$options_after_hook" "$options_before" "precmd hook restores caller options"

unsetopt extendedglob nullglob
setopt nomatch
workspace_roots "$script_dir/fixture_root/missing"
eq "${#reply}" "0" "missing roots return an empty reply"
typeset options_after_missing="${options[extendedglob]}:${options[nullglob]}:${options[nomatch]}"
eq "$options_after_missing" "$options_before" \
  "early return restores caller options"

if (( fails > 0 )); then
  print -r -- "$fails of $checks checks failed"
  exit 1
fi
print -r -- "all $checks checks passed"
