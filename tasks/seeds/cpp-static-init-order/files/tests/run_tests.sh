#!/bin/sh
set -eu

if [ -n "${CXX:-}" ]; then
  compiler=$CXX
else
  compiler=
  for candidate in c++ clang++ g++; do
    if candidate_path=$(command -v "$candidate"); then
      compiler=$candidate_path
      break
    fi
  done
fi

if [ -z "$compiler" ]; then
  echo "no C++ compiler found" >&2
  exit 2
fi

# Some minimal Clang installations keep libc++ beside the compiler without
# making it the default. Select that colocated runtime when it is available.
stdlib_flags=
linker_flags=
case $("$compiler" --version | sed -n '1p') in
  *clang*)
    compiler_path=$(command -v "$compiler")
    compiler_prefix=$(CDPATH= cd -- "$(dirname "$compiler_path")/.." && pwd)
    if [ -d "$compiler_prefix/include/c++/v1" ] &&
       [ -e "$compiler_prefix/lib/libc++.so" ]; then
      stdlib_flags=-stdlib=libc++
      linker_flags="-L$compiler_prefix/lib -Wl,-rpath,$compiler_prefix/lib"
    fi
    ;;
esac

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/cpp-static-init-order.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM

common_flags="-std=c++17 -Wall -Wextra -Werror -pedantic -Iinclude $stdlib_flags"

# Compile once so that only object-file link order differs between the probes.
# shellcheck disable=SC2086
"$compiler" $common_flags -c src/content_type_lookup.cpp -o "$build_dir/registry.o"
# shellcheck disable=SC2086
"$compiler" $common_flags -c src/builtins.cpp -o "$build_dir/builtins.o"
# shellcheck disable=SC2086
"$compiler" $common_flags -c tests/lookup_probe.cpp -o "$build_dir/probe.o"

# shellcheck disable=SC2086
"$compiler" $stdlib_flags $linker_flags \
  "$build_dir/registry.o" "$build_dir/builtins.o" \
  "$build_dir/probe.o" -o "$build_dir/registry-first"
# shellcheck disable=SC2086
"$compiler" $stdlib_flags $linker_flags \
  "$build_dir/builtins.o" "$build_dir/registry.o" \
  "$build_dir/probe.o" -o "$build_dir/builtins-first"

expected='json=application/json
txt=text/plain
unknown=<missing>'

check_order() {
  label=$1
  executable=$2
  actual=$("$executable")
  if [ "$actual" != "$expected" ]; then
    echo "$label link order changed public lookup results" >&2
    echo "expected:" >&2
    printf '%s\n' "$expected" >&2
    echo "actual:" >&2
    printf '%s\n' "$actual" >&2
    return 1
  fi
}

check_order "registry-first" "$build_dir/registry-first"
check_order "builtins-first" "$build_dir/builtins-first"

echo "both controlled link orders preserve lookup behavior"
