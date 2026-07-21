cmake_minimum_required(VERSION 3.14)
project(cpp_ownership_raii_move LANGUAGES CXX)

add_library(pipeline_owner src/pipeline.cpp)
target_include_directories(pipeline_owner PUBLIC include)
target_compile_features(pipeline_owner PUBLIC cxx_std_17)

enable_testing()
add_executable(ownership_contract_test tests/ownership_contract_test.cpp)
target_link_libraries(ownership_contract_test PRIVATE pipeline_owner)
add_test(NAME ownership_contract COMMAND ownership_contract_test)
