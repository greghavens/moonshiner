#!/usr/bin/env bash
# photosort.sh — file a camera-card dump into the photo library.
#
# Usage: photosort.sh <card-dir> <library-dir>
#
# Shots named "YYYY-MM-DD <title>.<ext>" are filed under <library>/YYYY-MM/;
# anything without a date prefix goes to <library>/unsorted/. The library
# index (index/index.txt) is rebuilt at the end of every run.

card_dir=$1
library=$2

if [ -z "$card_dir" ] || [ -z "$library" ]; then
  echo "usage: photosort.sh <card-dir> <library-dir>" >&2
  exit 64
fi

mkdir -p $library/unsorted

cd $card_dir

sorted=0
for shot in *.jpg *.png; do
  [ -e "$shot" ] || continue
  case $shot in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]" "*)
      dest=$library/${shot:0:7}
      ;;
    *)
      dest=$library/unsorted
      ;;
  esac
  mkdir -p $dest
  mv $shot $dest/
  sorted=$((sorted + 1))
done

index_file=$library/index/index.txt
mkdir -p $(dirname $index_file)
(cd $library && find . -type f ! -path './index/*' | sed 's|^\./||' | LC_ALL=C sort) > $index_file

echo "sorted $sorted file(s)"
