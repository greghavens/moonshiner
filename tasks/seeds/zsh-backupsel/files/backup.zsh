#!/usr/bin/env zsh
# backup.zsh — copy every plain file in a source tree into a backup drop.
#
#   zsh backup.zsh <src-dir> <dest-dir>
#
# Walks the source with zsh globbing (no find), copies each plain file to the
# same relative path under the destination, and writes manifest.txt there —
# one relative path per line, byte order — so two drops can be diffed.
# Dotfiles and dot-directories stay behind on purpose.
emulate -R zsh
setopt no_unset extended_glob

# manifest order is part of the contract; pin collation
LC_ALL=C
export LC_ALL

usage() {
  print -u2 -- 'usage: backup.zsh <src-dir> <dest-dir>'
  exit 64
}

(( $# == 2 )) || usage
src=$1
dest=$2
if [[ ! -d $src ]]; then
  print -u2 -- "backup.zsh: not a directory: $src"
  exit 66
fi

typeset -a rels
typeset f
for f in $src/**/*(.N); do
  rels+=( "${f#$src/}" )
done
rels=( "${(o)rels[@]}" )

mkdir -p -- "$dest"
typeset rel
for rel in "${rels[@]}"; do
  if [[ $rel == */* ]]; then
    mkdir -p -- "$dest/${rel:h}"
  fi
  cp -- "$src/$rel" "$dest/$rel"
done

: > "$dest/manifest.txt"
for rel in "${rels[@]}"; do
  print -r -- "$rel" >> "$dest/manifest.txt"
done

print -r -- "files copied: ${#rels}"
