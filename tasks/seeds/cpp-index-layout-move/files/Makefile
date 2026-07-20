CXX = clang++
LLVM_PREFIX = $(abspath $(shell $(CXX) --print-resource-dir)/../../..)
CXXFLAGS = -std=c++17 -stdlib=libc++ -O2 -Wall -Wextra -Werror -pedantic -Iinclude
LDFLAGS = -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib \
	-Wl,--wrap=fsync -Wl,--wrap=rename

TEST_BINARY = build/test_index_migration

.PHONY: all test clean

all: $(TEST_BINARY)

$(TEST_BINARY): src/index_store.cpp include/index_store.hpp protected_tests/test_index_migration.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) src/index_store.cpp protected_tests/test_index_migration.cpp $(LDFLAGS) -o $(TEST_BINARY)

test: $(TEST_BINARY)
	./$(TEST_BINARY)

clean:
	rm -rf build test-tmp
