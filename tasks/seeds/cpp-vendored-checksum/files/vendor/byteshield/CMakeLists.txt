cmake_minimum_required(VERSION 3.16)

project(ByteShield VERSION 2.3.0 LANGUAGES C)

option(BYTESHIELD_BUILD_TESTS "Build ByteShield's upstream tests" ON)
option(BYTESHIELD_BUILD_TOOLS "Build ByteShield's command-line tools" ON)
option(BYTESHIELD_INSTALL "Install ByteShield as a standalone package" ON)
option(BYTESHIELD_NATIVE_UNALIGNED "Use native unaligned word loads" ON)

add_library(byteshield OBJECT src/byteshield.c)
add_library(byteshield::byteshield ALIAS byteshield)
target_compile_features(byteshield PUBLIC c_std_99)
target_include_directories(byteshield PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)
if(BYTESHIELD_NATIVE_UNALIGNED)
    target_compile_definitions(byteshield PRIVATE BYTESHIELD_NATIVE_UNALIGNED=1)
endif()

if(BYTESHIELD_BUILD_TOOLS)
    add_executable(byteshield-sum src/byteshield_tool.c)
    target_link_libraries(byteshield-sum PRIVATE byteshield)
endif()

if(BYTESHIELD_BUILD_TESTS)
    add_executable(byteshield-selftest tests/selftest.c)
    target_link_libraries(byteshield-selftest PRIVATE byteshield)
endif()

if(BYTESHIELD_INSTALL)
    install(FILES include/byteshield/byteshield.h DESTINATION include/byteshield)
endif()
