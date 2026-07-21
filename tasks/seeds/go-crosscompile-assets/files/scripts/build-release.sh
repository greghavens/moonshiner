#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

go generate ./internal/assets

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "release build requires a Git work tree for generated-asset checks" >&2
	exit 1
fi
if ! git diff --quiet HEAD -- internal/assets/generated; then
	echo "generated assets are not up to date; run go generate ./internal/assets and commit the result" >&2
	exit 1
fi
untracked=$(git ls-files --others --exclude-standard -- internal/assets/generated)
if [ -n "$untracked" ]; then
	echo "generated assets are not up to date; generated files are untracked" >&2
	exit 1
fi

dist_dir=${DIST_DIR:-"$project_root/dist"}
mkdir -p "$dist_dir"

export CGO_ENABLED=0
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1700000000}

for target in linux/amd64 windows/amd64; do
	goos=${target%/*}
	goarch=${target#*/}
	suffix=
	if [ "$goos" = windows ]; then
		suffix=.exe
	fi
	artifact="$dist_dir/distill-$goos-$goarch$suffix"
	GOOS=$goos GOARCH=$goarch go build \
		-trimpath \
		-buildvcs=false \
		-ldflags=-buildid= \
		-o "$artifact" \
		./cmd/distill
done
