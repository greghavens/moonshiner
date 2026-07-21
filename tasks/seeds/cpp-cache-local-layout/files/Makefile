CXX = clang++
LLVM_PREFIX = $(abspath $(shell $(CXX) --print-resource-dir)/../../..)
CXXFLAGS = -std=c++17 -stdlib=libc++ -O2 -Wall -Wextra -Werror -pedantic -Iinclude
LDFLAGS = -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib

TEST_BINARIES = build/behavior_tests build/layout_benchmark

.PHONY: all test clean

all: $(TEST_BINARIES)

build/behavior_tests: src/cache_chain.cpp include/cache_chain.hpp tests/behavior_tests.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) src/cache_chain.cpp tests/behavior_tests.cpp $(LDFLAGS) -o $@

build/layout_benchmark: src/cache_chain.cpp include/cache_chain.hpp tests/layout_benchmark.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) src/cache_chain.cpp tests/layout_benchmark.cpp $(LDFLAGS) -o $@

test: $(TEST_BINARIES)
	./build/behavior_tests
	./build/layout_benchmark

clean:
	rm -rf build
