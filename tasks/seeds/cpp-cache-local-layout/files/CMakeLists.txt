cmake_minimum_required(VERSION 3.16)
project(cache_local_layout LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(cache_chain src/cache_chain.cpp)
target_include_directories(cache_chain PUBLIC include)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(cache_chain PRIVATE -Wall -Wextra -Wpedantic)
endif()

enable_testing()

add_executable(behavior_tests tests/behavior_tests.cpp)
target_link_libraries(behavior_tests PRIVATE cache_chain)
add_test(NAME behavior_tests COMMAND behavior_tests)

add_executable(layout_benchmark tests/layout_benchmark.cpp)
target_link_libraries(layout_benchmark PRIVATE cache_chain)
add_test(NAME layout_benchmark COMMAND layout_benchmark)
