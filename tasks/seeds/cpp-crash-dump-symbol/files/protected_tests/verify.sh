#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_root=$(mktemp -d)
trap 'rm -rf -- "$build_root"' EXIT

cd "$repo_root"

if command -v clang++ >/dev/null 2>&1; then
  compiler=clang++
else
  compiler=g++
fi

"$compiler" \
  -std=c++20 \
  -O1 \
  -g \
  -Wall \
  -Wextra \
  -Werror \
  -Wpedantic \
  -fsanitize=address,undefined \
  -fno-omit-frame-pointer \
  -Iinclude \
  src/event_loop.cpp \
  src/session.cpp \
  src/runtime.cpp \
  protected_tests/lifecycle_test.cpp \
  -o "$build_root/lifecycle_test"

ASAN_OPTIONS=detect_leaks=0:halt_on_error=1:abort_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
  "$build_root/lifecycle_test"
