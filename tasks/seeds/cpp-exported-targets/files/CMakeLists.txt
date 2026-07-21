cmake_minimum_required(VERSION 3.20)

project(BeaconQueue VERSION 1.4.0 LANGUAGES CXX)

include(CTest)
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

find_package(Threads REQUIRED)

add_library(beacon_queue STATIC src/queue.cpp)
add_library(Beacon::queue ALIAS beacon_queue)
target_compile_features(beacon_queue PUBLIC cxx_std_17)
target_include_directories(beacon_queue
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
        "$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>"
)
target_link_libraries(beacon_queue PUBLIC Threads::Threads)
set_target_properties(beacon_queue PROPERTIES
    EXPORT_NAME queue
    OUTPUT_NAME beacon_queue
)

if(BUILD_TESTING)
    add_executable(beacon_in_tree_test tests/in_tree.cpp)
    target_link_libraries(beacon_in_tree_test PRIVATE Beacon::queue)
    add_test(NAME beacon.in_tree COMMAND beacon_in_tree_test)
endif()

set(beacon_package_dir "${CMAKE_INSTALL_LIBDIR}/cmake/BeaconQueue")
configure_package_config_file(
    cmake/BeaconQueueConfig.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/BeaconQueueConfig.cmake"
    INSTALL_DESTINATION "${beacon_package_dir}"
)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/BeaconQueueConfigVersion.cmake"
    VERSION "${PROJECT_VERSION}"
    COMPATIBILITY SameMajorVersion
)

install(TARGETS beacon_queue
    EXPORT BeaconQueueTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    INCLUDES DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}"
)
install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(EXPORT BeaconQueueTargets
    FILE BeaconQueueTargets.cmake
    NAMESPACE Beacon::
    DESTINATION "${beacon_package_dir}"
)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/BeaconQueueConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/BeaconQueueConfigVersion.cmake"
    DESTINATION "${beacon_package_dir}"
)
