#!/usr/bin/env bash
set -eu

build_dir=.moonshiner-test-build
test_binary="$build_dir/length-prefixed-decoder-tests"

cleanup() {
  rm -f "$test_binary"
  rmdir "$build_dir" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$build_dir"
clang++ -std=c++20 -stdlib=libc++ -Wall -Wextra -Wpedantic -Werror \
  -Iinclude tests/length_prefixed_decoder_test.cpp \
  -L/home/linuxbrew/.linuxbrew/lib \
  -Wl,-rpath,/home/linuxbrew/.linuxbrew/lib \
  -o "$test_binary"
"$test_binary"
