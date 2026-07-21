CXX := clang++
CPPFLAGS := -Iinclude -Itests
CXXFLAGS := -std=c++17 -stdlib=libc++ -Wall -Wextra -Werror -pedantic -O2
LLVM_LIBDIR := $(shell $(CXX) --print-resource-dir)/../..
LDFLAGS := -L$(LLVM_LIBDIR) -Wl,-rpath,$(LLVM_LIBDIR)
BUILD_DIR := build
TEST_BINARY := $(BUILD_DIR)/snapshot_store_tests
SOURCES := src/snapshot_store.cpp tests/public_tests.cpp tests/protected_tests.cpp

.PHONY: all test clean

all: $(TEST_BINARY)

$(TEST_BINARY): $(SOURCES) include/snapshot_store.hpp tests/test_support.hpp
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SOURCES) $(LDFLAGS) -o $(TEST_BINARY)

test: $(TEST_BINARY)
	./$(TEST_BINARY)

clean:
	rm -rf -- $(BUILD_DIR) .test-tmp
