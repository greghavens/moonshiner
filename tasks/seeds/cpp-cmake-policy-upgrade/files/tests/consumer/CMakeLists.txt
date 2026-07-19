cmake_minimum_required(VERSION 3.21)
project(TelemetryConsumer LANGUAGES CXX)

find_package(TelemetryCodec CONFIG REQUIRED)
add_executable(telemetry_consumer main.cpp)
target_link_libraries(telemetry_consumer PRIVATE Telemetry::codec)
