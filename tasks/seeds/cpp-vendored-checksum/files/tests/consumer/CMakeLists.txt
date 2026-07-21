cmake_minimum_required(VERSION 3.20)

project(StreamSealConsumer LANGUAGES CXX)

find_package(StreamSeal 3 CONFIG REQUIRED)

add_executable(streamseal_consumer main.cpp)
target_link_libraries(streamseal_consumer PRIVATE StreamSeal::streamseal)
