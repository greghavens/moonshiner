#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work_dir="$project_dir/.acceptance"

rm -rf "$work_dir"
mkdir -p "$work_dir"

python3 "$project_dir/tests/check_vendor_contract.py"

build_and_consume_cmake() {
    mode=$1
    shared=$2
    build_dir="$work_dir/build-$mode"
    install_dir="$work_dir/install-$mode"
    moved_dir="$work_dir/moved-$mode"
    consumer_build="$work_dir/consumer-$mode"

    cmake -S "$project_dir" -B "$build_dir" \
        -DBUILD_SHARED_LIBS="$shared" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_INSTALL_PREFIX="$install_dir" \
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
        -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
    cmake --build "$build_dir" --parallel 2
    ctest --test-dir "$build_dir" --output-on-failure

    for option in BYTESHIELD_BUILD_TESTS BYTESHIELD_BUILD_TOOLS \
                  BYTESHIELD_INSTALL BYTESHIELD_NATIVE_UNALIGNED; do
        grep -q "^${option}:BOOL=OFF$" "$build_dir/CMakeCache.txt"
    done
    test ! -e "$build_dir/vendor/byteshield/byteshield-sum"
    test ! -e "$build_dir/vendor/byteshield/byteshield-selftest"

    cmake --install "$build_dir"
    mv "$install_dir" "$moved_dir"
    test ! -e "$moved_dir/include/byteshield"
    if grep -R -F "$project_dir" "$moved_dir" >/dev/null 2>&1; then
        echo "installed package leaks its source path" >&2
        return 1
    fi
    if grep -R -E 'byteshield(::|[.]h)' "$moved_dir/lib/cmake/StreamSeal" \
            >/dev/null 2>&1; then
        echo "installed target leaks the vendored dependency" >&2
        return 1
    fi

    cmake -S "$project_dir/tests/consumer" -B "$consumer_build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH="$moved_dir" \
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
        -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
    cmake --build "$consumer_build" --parallel 2
    output=$($consumer_build/streamseal_consumer)
    test "$output" = "316f6513"
}

build_and_consume_native() {
    mode=$1
    build_dir="$work_dir/build-$mode"
    install_dir="$work_dir/install-$mode"
    moved_dir="$work_dir/moved-$mode"
    consumer_build="$work_dir/consumer-$mode"

    cc=$(command -v cc || command -v clang || command -v gcc) || {
        echo "acceptance requires a C compiler" >&2
        return 1
    }
    cxx=$(command -v c++ || command -v clang++ || command -v g++) || {
        echo "acceptance requires a C++ compiler" >&2
        return 1
    }

    mkdir -p "$build_dir" "$install_dir/include/streamseal" \
        "$install_dir/lib" "$consumer_build"

    "$cc" -std=c99 -O2 -fPIC -fvisibility=hidden \
        -I"$project_dir/vendor/byteshield/include" \
        -c "$project_dir/vendor/byteshield/src/byteshield.c" \
        -o "$build_dir/byteshield.o"
    "$cxx" -std=c++17 -O2 -fPIC -fvisibility=hidden \
        -fvisibility-inlines-hidden -DSTREAMSEAL_BUILDING \
        -I"$project_dir/include" \
        -I"$project_dir/vendor/byteshield/include" \
        -c "$project_dir/src/streamseal.cpp" \
        -o "$build_dir/streamseal.o"

    if test "$mode" = shared; then
        "$cc" -shared -Wl,-soname,libstreamseal.so.3 \
            "$build_dir/streamseal.o" "$build_dir/byteshield.o" \
            -o "$build_dir/libstreamseal.so.3.4.1"
        ln -s libstreamseal.so.3.4.1 "$build_dir/libstreamseal.so.3"
        ln -s libstreamseal.so.3 "$build_dir/libstreamseal.so"
        link_args="-L$build_dir -lstreamseal -Wl,-rpath,$build_dir"
    else
        ar rcs "$build_dir/libstreamseal.a" \
            "$build_dir/streamseal.o" "$build_dir/byteshield.o"
        link_args="$build_dir/libstreamseal.a"
    fi

    "$cxx" -std=c++17 -O2 -I"$project_dir/include" \
        -c "$project_dir/tests/in_tree.cpp" \
        -o "$build_dir/in_tree.o"
    # Splitting link_args is intentional: it holds compiler arguments, not paths
    # supplied by the candidate solution.
    # shellcheck disable=SC2086
    "$cc" "$build_dir/in_tree.o" $link_args \
        -o "$build_dir/streamseal_in_tree"
    "$build_dir/streamseal_in_tree"

    install -m 0644 "$project_dir/include/streamseal/streamseal.h" \
        "$install_dir/include/streamseal/streamseal.h"
    if test "$mode" = shared; then
        install -m 0755 "$build_dir/libstreamseal.so.3.4.1" \
            "$install_dir/lib/libstreamseal.so.3.4.1"
        ln -s libstreamseal.so.3.4.1 "$install_dir/lib/libstreamseal.so.3"
        ln -s libstreamseal.so.3 "$install_dir/lib/libstreamseal.so"
    else
        install -m 0644 "$build_dir/libstreamseal.a" \
            "$install_dir/lib/libstreamseal.a"
    fi

    mv "$install_dir" "$moved_dir"
    test ! -e "$moved_dir/include/byteshield"
    if grep -R -F "$project_dir" "$moved_dir" >/dev/null 2>&1; then
        echo "installed package leaks its source path" >&2
        return 1
    fi

    if test "$mode" = shared; then
        consumer_link_args="-L$moved_dir/lib -lstreamseal -Wl,-rpath,$moved_dir/lib"
    else
        consumer_link_args="$moved_dir/lib/libstreamseal.a"
    fi
    "$cxx" -std=c++17 -O2 -I"$moved_dir/include" \
        -c "$project_dir/tests/consumer/main.cpp" \
        -o "$consumer_build/main.o"
    # shellcheck disable=SC2086
    "$cc" "$consumer_build/main.o" $consumer_link_args \
        -o "$consumer_build/streamseal_consumer"
    output=$($consumer_build/streamseal_consumer)
    test "$output" = "316f6513"
}

if command -v cmake >/dev/null 2>&1 && command -v ctest >/dev/null 2>&1; then
    build_and_consume_cmake static OFF
    build_and_consume_cmake shared ON
else
    echo "acceptance: CMake unavailable; using equivalent native build checks"
    build_and_consume_native static
    build_and_consume_native shared
fi

shared_library=$(find "$work_dir/moved-shared/lib" -maxdepth 1 \
    -type f -name 'libstreamseal.so.*' | sort | head -n 1)
test -n "$shared_library"

symbols=$(nm -D --defined-only "$shared_library" | awk '{print $3}' | sort)
test "$symbols" = "streamseal_abi_version
streamseal_checksum"

soname=$(readelf -d "$shared_library" | sed -n 's/.*SONAME.*\[\(.*\)\].*/\1/p')
test "$soname" = "libstreamseal.so.3"

echo "acceptance: ok"
