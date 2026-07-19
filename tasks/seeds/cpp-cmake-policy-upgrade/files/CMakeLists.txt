cmake_minimum_required(VERSION 3.12)

project(TelemetryCodec VERSION 2.4.0 LANGUAGES CXX)

option(BUILD_SHARED_LIBS "Build TelemetryCodec as a shared library" OFF)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++20 -Wall -Wextra -Werror")
include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")

add_library(telemetry_codec src/codec.cpp)
set_target_properties(telemetry_codec PROPERTIES
    EXPORT_NAME codec
    OUTPUT_NAME telemetry_codec
)

include(GNUInstallDirs)
configure_file(
    cmake/TelemetryCodecConfig.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/TelemetryCodecConfig.cmake"
    @ONLY
)

install(TARGETS telemetry_codec
    EXPORT TelemetryCodecTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)
install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(EXPORT TelemetryCodecTargets
    FILE TelemetryCodecTargets.cmake
    NAMESPACE Telemetry::
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/TelemetryCodec"
)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/TelemetryCodecConfig.cmake"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/TelemetryCodec"
)
