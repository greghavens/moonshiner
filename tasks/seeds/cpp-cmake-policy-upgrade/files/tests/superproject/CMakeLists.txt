cmake_minimum_required(VERSION 3.21)
project(TelemetryParent LANGUAGES CXX)

set(BUILD_SHARED_LIBS ON)
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../.." telemetry-from-parent)

get_target_property(telemetry_type telemetry_codec TYPE)
if(NOT telemetry_type STREQUAL "SHARED_LIBRARY")
    message(FATAL_ERROR
        "CMP0077 parent override was lost: telemetry_codec is ${telemetry_type}")
endif()
