cmake_minimum_required(VERSION 3.20)

project(BeaconConsumer LANGUAGES CXX)

find_package(BeaconQueue 1.4 CONFIG REQUIRED)

if(NOT TARGET Beacon::queue)
    message(FATAL_ERROR "installed package did not define Beacon::queue")
endif()
if(TARGET beacon_queue)
    message(FATAL_ERROR "installed package leaked an unnamespaced build-tree target")
endif()
if(NOT TARGET Threads::Threads)
    message(FATAL_ERROR "BeaconQueue did not discover its Threads dependency")
endif()

get_target_property(beacon_links Beacon::queue INTERFACE_LINK_LIBRARIES)
list(FIND beacon_links "Threads::Threads" threads_link_index)
if(threads_link_index EQUAL -1)
    message(FATAL_ERROR
        "Beacon::queue lost its transitive Threads::Threads dependency")
endif()

add_executable(beacon_consumer main.cpp)
target_link_libraries(beacon_consumer PRIVATE Beacon::queue)
