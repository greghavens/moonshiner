#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$project_dir/.ownership-test-build"
rm -rf -- "$build_dir"
mkdir -p "$build_dir"
trap 'rm -rf -- "$build_dir"' EXIT

compiler=""
compiler_args=()
if [[ -n "${CXX:-}" ]]; then
  candidates=("$CXX")
else
  candidates=(c++ g++ clang++)
fi

for candidate in "${candidates[@]}"; do
  if ! command -v "$candidate" >/dev/null 2>&1; then
    continue
  fi
  if printf '#include <memory>\nint main() { return 0; }\n' | \
      "$candidate" -std=c++17 -x c++ - -o "$build_dir/compiler_probe" \
      >/dev/null 2>&1; then
    compiler="$candidate"
    break
  fi
  resource_dir="$("$candidate" -print-resource-dir 2>/dev/null || true)"
  if [[ -n "$resource_dir" ]]; then
    library_dir="$(dirname "$(dirname "$resource_dir")")"
    libcxx_args=(-stdlib=libc++ "-L$library_dir" "-Wl,-rpath,$library_dir")
    if printf '#include <memory>\nint main() { return 0; }\n' | \
        "$candidate" -std=c++17 "${libcxx_args[@]}" -x c++ - \
        -o "$build_dir/compiler_probe" >/dev/null 2>&1; then
      compiler="$candidate"
      compiler_args=("${libcxx_args[@]}")
      break
    fi
  fi
done

if [[ -z "$compiler" ]]; then
  echo "error: no working C++17 compiler found" >&2
  exit 1
fi

"$compiler" \
  -std=c++17 \
  "${compiler_args[@]}" \
  -Wall -Wextra -Werror -pedantic \
  -I"$project_dir/include" \
  "$project_dir/src/pipeline.cpp" \
  "$project_dir/tests/ownership_contract_test.cpp" \
  -o "$build_dir/ownership_contract_test"

"$build_dir/ownership_contract_test"
