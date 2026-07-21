#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# The validation image exposes its C++ toolchain through an installed Flatpak
# SDK, but does not put CMake on PATH. Re-enter that SDK when a native CMake
# installation is unavailable; ordinary development environments take the
# direct path above and never need bwrap.
if ! command -v cmake >/dev/null 2>&1 || ! command -v ctest >/dev/null 2>&1; then
  if [[ -n "${BEACON_CMAKE_BOOTSTRAPPED:-}" ]]; then
    echo "CMake and CTest are required to run the acceptance suite" >&2
    exit 1
  fi
  if ! command -v bwrap >/dev/null 2>&1; then
    echo "CMake and CTest are required to run the acceptance suite" >&2
    exit 1
  fi

  sdk_base="/var/lib/flatpak/runtime/org.freedesktop.Sdk/$(uname -m)"
  mapfile -t sdk_cmake_candidates < <(
    compgen -G "$sdk_base/*/*/files/bin/cmake" | sort
  )
  if ((${#sdk_cmake_candidates[@]} == 0)); then
    echo "CMake and CTest are required to run the acceptance suite" >&2
    exit 1
  fi

  last_sdk_index=$((${#sdk_cmake_candidates[@]} - 1))
  sdk_files=${sdk_cmake_candidates[$last_sdk_index]%/bin/cmake}
  if [[ ! -x "$sdk_files/bin/ctest" || ! -x "$sdk_files/bin/c++" ]]; then
    echo "the installed Flatpak SDK lacks the required CMake toolchain" >&2
    exit 1
  fi

  exec bwrap \
    --ro-bind / / \
    --ro-bind "$sdk_files" /usr \
    --dev-bind /dev /dev \
    --proc /proc \
    --bind "$root_dir" "$root_dir" \
    --bind /tmp /tmp \
    --setenv PATH /usr/bin:/bin \
    --setenv BEACON_CMAKE_BOOTSTRAPPED 1 \
    --chdir "$root_dir" \
    /usr/bin/bash "$root_dir/tests/run_acceptance.sh"
fi

verify_dir="$root_dir/_verify"
build_dir="$verify_dir/build"
original_prefix="$verify_dir/original-prefix"
relocated_prefix="$verify_dir/relocated-prefix"
package_dir="$relocated_prefix/lib/cmake/BeaconQueue"

rm -rf "$verify_dir"
mkdir -p "$verify_dir"

cmake -S "$root_dir" -B "$build_dir" \
  -DBUILD_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_INSTALL_PREFIX="$original_prefix" \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
  -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
cmake --build "$build_dir"
ctest --test-dir "$build_dir" --output-on-failure
cmake --install "$build_dir"

mv "$original_prefix" "$relocated_prefix"
test -f "$package_dir/BeaconQueueConfig.cmake"
test -f "$package_dir/BeaconQueueTargets.cmake"
grep -F 'Beacon::queue' "$package_dir/BeaconQueueTargets.cmake" >/dev/null
grep -F 'Threads::Threads' "$package_dir/BeaconQueueTargets.cmake" >/dev/null

if grep -R -F "$root_dir" "$package_dir"; then
  echo "installed package metadata leaked its source or original install path" >&2
  exit 1
fi

cmake -S "$root_dir/tests/consumer" -B "$verify_dir/consumer-build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$relocated_prefix" \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
  -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
cmake --build "$verify_dir/consumer-build"

consumer_output=$($verify_dir/consumer-build/beacon_consumer)
test "$consumer_output" = "jobs=4,total=19"

python3 "$root_dir/tests/check_package_contract.py"
echo "installed Beacon::queue relocation and dependency checks passed"
