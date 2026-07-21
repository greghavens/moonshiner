#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "${build_dir}"' EXIT

if [[ -n "${CXX:-}" ]]; then
  compiler="${CXX}"
elif compiler="$(type -P c++)"; then
  :
elif compiler="$(type -P clang++)"; then
  :
else
  echo "no C++ compiler found" >&2
  exit 1
fi

common_flags=(-std=c++17 -Wall -Wextra -Werror -pedantic -I"${project_dir}/include")

# Some hermetic images provide Clang and libc++ without a default libstdc++
# sysroot. Select that adjacent libc++ only when the compiler's normal C++
# include lookup is unavailable.
if ! printf '#include <string>\n' | \
    "${compiler}" -std=c++17 -x c++ -fsyntax-only - >/dev/null 2>&1; then
  resource_dir="$("${compiler}" --print-resource-dir)"
  llvm_root="$(cd "${resource_dir}/../../.." && pwd)"
  if [[ ! -f "${llvm_root}/include/c++/v1/string" ||
        ! -f "${llvm_root}/lib/libc++.so" ]]; then
    echo "compiler has no usable C++ standard library" >&2
    exit 1
  fi
  common_flags+=(
    -stdlib=libc++
    -L"${llvm_root}/lib"
    "-Wl,-rpath,${llvm_root}/lib"
  )
fi

"${compiler}" "${common_flags[@]}" \
  -DORBIT_ACCELERATOR_COMPILED=1 \
  -DORBIT_TELEMETRY_COMPILED=1 \
  "${project_dir}/src/application.cpp" \
  "${project_dir}/tests/protected/startup_capabilities_test.cpp" \
  -o "${build_dir}/startup_capabilities_test"
"${build_dir}/startup_capabilities_test"

"${compiler}" "${common_flags[@]}" \
  -DORBIT_ACCELERATOR_COMPILED=0 \
  -DORBIT_TELEMETRY_COMPILED=0 \
  "${project_dir}/src/application.cpp" \
  "${project_dir}/tests/protected/not_compiled_test.cpp" \
  -o "${build_dir}/not_compiled_test"
"${build_dir}/not_compiled_test"

for compiled_capability in accelerator telemetry; do
  accelerator_compiled=0
  telemetry_compiled=0
  if [[ "${compiled_capability}" == accelerator ]]; then
    accelerator_compiled=1
  else
    telemetry_compiled=1
  fi

  "${compiler}" "${common_flags[@]}" \
    -DORBIT_ACCELERATOR_COMPILED="${accelerator_compiled}" \
    -DORBIT_TELEMETRY_COMPILED="${telemetry_compiled}" \
    "${project_dir}/src/application.cpp" \
    "${project_dir}/tests/protected/mixed_compilation_test.cpp" \
    -o "${build_dir}/${compiled_capability}_compiled_test"
  "${build_dir}/${compiled_capability}_compiled_test"
done
