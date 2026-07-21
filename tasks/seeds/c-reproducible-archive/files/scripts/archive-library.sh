#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: archive-library.sh ARCHIVE OBJECT-DIRECTORY" >&2
    exit 64
fi

archive=$1
object_dir=$2
: "${AR:=ar}"

rm -f "$archive"

# Preserve compiler completion order to make post-build inspection convenient.
# GNU ar's U modifier also carries the original member metadata into the file.
find "$object_dir" -type f -name '*.o' -printf '%T@ %p\0' |
    sort -z -n |
    cut -z -d ' ' -f 2- |
    xargs -0 "$AR" rcsU "$archive"
