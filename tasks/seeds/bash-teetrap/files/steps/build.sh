#!/usr/bin/env bash
# build step: bundle the source snippets into out/bundle.txt
set -u

files=(src/*.txt)
echo "build: scanning ${#files[@]} source file(s)"

bad=0
for f in "${files[@]}"; do
  if [ ! -s "$f" ]; then
    echo "build: error: empty source file: $f" >&2
    bad=1
  fi
done
[ "$bad" -eq 0 ] || exit 3

mkdir -p out
{
  echo '== bundle =='
  cat "${files[@]}"
} > out/bundle.txt
echo "build: bundled ${#files[@]} file(s)"
