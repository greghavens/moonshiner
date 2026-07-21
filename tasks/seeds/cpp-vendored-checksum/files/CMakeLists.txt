cmake_minimum_required(VERSION 3.20)

project(StreamSeal VERSION 3.4.1 LANGUAGES C CXX)

option(BUILD_SHARED_LIBS "Build StreamSeal as a shared library" OFF)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/VendorByteShield.cmake")

streamseal_add_vendored_byteshield()

add_library(streamseal src/streamseal.cpp)
add_library(StreamSeal::streamseal ALIAS streamseal)

target_compile_features(streamseal PUBLIC cxx_std_17)
target_compile_definitions(streamseal PRIVATE STREAMSEAL_BUILDING)
target_include_directories(streamseal
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
        "$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>"
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/vendor/byteshield/include"
)
target_sources(streamseal PRIVATE $<TARGET_OBJECTS:byteshield>)
set_target_properties(streamseal PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
    VERSION "${PROJECT_VERSION}"
    SOVERSION 3
    EXPORT_NAME streamseal
)

enable_testing()
add_executable(streamseal_in_tree tests/in_tree.cpp)
target_link_libraries(streamseal_in_tree PRIVATE StreamSeal::streamseal)
add_test(NAME streamseal_in_tree COMMAND streamseal_in_tree)

install(TARGETS streamseal
    EXPORT StreamSealTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)
install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(EXPORT StreamSealTargets
    FILE StreamSealTargets.cmake
    NAMESPACE StreamSeal::
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/StreamSeal"
)

configure_package_config_file(
    cmake/StreamSealConfig.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/StreamSealConfig.cmake"
    INSTALL_DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/StreamSeal"
)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/StreamSealConfigVersion.cmake"
    VERSION "${PROJECT_VERSION}"
    COMPATIBILITY SameMajorVersion
)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/StreamSealConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/StreamSealConfigVersion.cmake"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/StreamSeal"
)
