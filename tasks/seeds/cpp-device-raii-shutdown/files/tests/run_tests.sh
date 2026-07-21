#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${CXX:-}" ]]; then
  compiler="$CXX"
elif command -v g++ >/dev/null 2>&1; then
  compiler="g++"
elif command -v clang++ >/dev/null 2>&1; then
  compiler="clang++"
else
  echo "no C++ compiler found" >&2
  exit 1
fi

compiler_args=()
if [[ "$(basename "$compiler")" == clang++ ]]; then
  resource_dir="$("$compiler" -print-resource-dir)"
  clang_prefix="$(cd "$resource_dir/../../.." && pwd -P)"
  if [[ -f "$clang_prefix/lib/libc++.so" ]]; then
    compiler_args+=(
      -stdlib=libc++
      "-L$clang_prefix/lib"
      "-Wl,-rpath,$clang_prefix/lib"
    )
  fi
fi

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/cpp-device-raii-shutdown.XXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

"$compiler" \
  "${compiler_args[@]}" \
  -std=c++17 \
  -Wall \
  -Wextra \
  -Werror \
  -pedantic \
  -Iinclude \
  src/device.cpp \
  tests/protected/device_shutdown_test.cpp \
  -o "$build_dir/device_shutdown_test"

"$build_dir/device_shutdown_test"
