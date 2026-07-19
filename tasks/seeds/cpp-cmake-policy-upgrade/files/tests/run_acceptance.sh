#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
accept_dir="$root_dir/_accept"
rm -rf "$accept_dir"
mkdir -p "$accept_dir"

cmake -S "$root_dir" -B "$accept_dir/static-build" \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$accept_dir/static-install"
cmake --build "$accept_dir/static-build"
cmake --install "$accept_dir/static-build"
test -f "$accept_dir/static-install/lib/libtelemetry_codec.a"

mv "$accept_dir/static-install" "$accept_dir/relocated-static"
if grep -R -F "$root_dir" "$accept_dir/relocated-static/lib/cmake/TelemetryCodec"; then
  echo "installed package leaked its source-tree path" >&2
  exit 1
fi
cmake -S "$root_dir/tests/consumer" -B "$accept_dir/static-consumer" \
  -DCMAKE_PREFIX_PATH="$accept_dir/relocated-static"
cmake --build "$accept_dir/static-consumer"
static_output=$($accept_dir/static-consumer/telemetry_consumer)
test "$static_output" = "7=120;9=-4;12=88"

cmake -S "$root_dir" -B "$accept_dir/shared-build" \
  -DBUILD_SHARED_LIBS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$accept_dir/shared-install"
cmake --build "$accept_dir/shared-build"
cmake --install "$accept_dir/shared-build"
test -f "$accept_dir/shared-install/lib/libtelemetry_codec.so"
cmake -S "$root_dir/tests/consumer" -B "$accept_dir/shared-consumer" \
  -DCMAKE_PREFIX_PATH="$accept_dir/shared-install"
cmake --build "$accept_dir/shared-consumer"
shared_output=$($accept_dir/shared-consumer/telemetry_consumer)
test "$shared_output" = "$static_output"

cmake -S "$root_dir/tests/superproject" -B "$accept_dir/super-build"
cmake --build "$accept_dir/super-build"

python3 "$root_dir/tests/check_cmake_contract.py"
echo "CMake static/shared install and consumer checks passed"
