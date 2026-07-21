include_guard(GLOBAL)

include("${CMAKE_CURRENT_LIST_DIR}/ByteShieldLock.cmake")

set(_STREAMSEAL_BYTESHIELD_SOURCE_FILES
    "CMakeLists.txt"
    "include/byteshield/byteshield.h"
    "src/byteshield.c"
)

function(_streamseal_sha256_manifest root output)
    set(_manifest "")
    foreach(_relative IN LISTS _STREAMSEAL_BYTESHIELD_SOURCE_FILES)
        set(_path "${root}/${_relative}")
        if(NOT EXISTS "${_path}")
            message(FATAL_ERROR "ByteShield source is incomplete: ${_relative}")
        endif()
        file(SHA256 "${_path}" _digest)
        string(APPEND _manifest "${_relative}:${_digest}\n")
    endforeach()
    string(SHA256 _tree_digest "${_manifest}")
    set(${output} "${_tree_digest}" PARENT_SCOPE)
endfunction()

function(streamseal_add_vendored_byteshield)
    set(_source_root "${PROJECT_SOURCE_DIR}/vendor/byteshield")
    set(_patch_root "${PROJECT_SOURCE_DIR}/vendor/patches")

    file(STRINGS "${_source_root}/include/byteshield/byteshield.h"
        _version_line REGEX "^#define BYTESHIELD_VERSION ")
    if(NOT _version_line STREQUAL
       "#define BYTESHIELD_VERSION \"${STREAMSEAL_BYTESHIELD_VERSION}\"")
        message(FATAL_ERROR
            "ByteShield version does not match lock: ${_version_line}")
    endif()

    _streamseal_sha256_manifest("${_source_root}" _source_digest)
    if(NOT _source_digest STREQUAL STREAMSEAL_BYTESHIELD_SOURCE_SHA256)
        message(FATAL_ERROR
            "ByteShield source checksum mismatch: ${_source_digest}")
    endif()

    file(SHA256 "${_source_root}/LICENSE" _license_digest)
    if(NOT _license_digest STREQUAL STREAMSEAL_BYTESHIELD_LICENSE_SHA256)
        message(FATAL_ERROR
            "ByteShield license checksum mismatch: ${_license_digest}")
    endif()

    foreach(_patch_record IN LISTS STREAMSEAL_BYTESHIELD_PATCHES)
        string(REPLACE "|" ";" _patch_fields "${_patch_record}")
        list(LENGTH _patch_fields _patch_field_count)
        if(NOT _patch_field_count EQUAL 2)
            message(FATAL_ERROR "Invalid ByteShield patch lock: ${_patch_record}")
        endif()
        list(GET _patch_fields 0 _patch_name)
        list(GET _patch_fields 1 _patch_expected)
        file(SHA256 "${_patch_root}/${_patch_name}" _patch_actual)
        if(NOT _patch_actual STREQUAL _patch_expected)
            message(FATAL_ERROR
                "ByteShield patch checksum mismatch for ${_patch_name}: ${_patch_actual}")
        endif()
    endforeach()

    file(READ "${_source_root}/src/byteshield.c" _patched_source)
    if(NOT _patched_source MATCHES "streamseal_vendor_byteshield_mix")
        message(FATAL_ERROR "ByteShield local symbol-prefix patch is not applied")
    endif()

    set(BYTESHIELD_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    set(BYTESHIELD_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
    set(BYTESHIELD_INSTALL OFF CACHE BOOL "" FORCE)
    set(BYTESHIELD_NATIVE_UNALIGNED OFF CACHE BOOL "" FORCE)
    add_subdirectory("${_source_root}"
        "${CMAKE_CURRENT_BINARY_DIR}/vendor/byteshield" EXCLUDE_FROM_ALL)

    if(NOT TARGET byteshield)
        message(FATAL_ERROR "ByteShield did not define its embedded target")
    endif()
    get_target_property(_byteshield_type byteshield TYPE)
    if(NOT _byteshield_type STREQUAL "OBJECT_LIBRARY")
        message(FATAL_ERROR "ByteShield must remain an embedded object target")
    endif()
    set_target_properties(byteshield PROPERTIES
        POSITION_INDEPENDENT_CODE ON
        C_VISIBILITY_PRESET hidden
    )
endfunction()
