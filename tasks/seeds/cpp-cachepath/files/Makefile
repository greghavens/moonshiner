CXX := $(shell command -v g++ 2>/dev/null || command -v clang++ 2>/dev/null || command -v c++ 2>/dev/null)
LLVM_PREFIX := $(abspath $(dir $(CXX))/..)
ifneq ($(wildcard $(LLVM_PREFIX)/include/c++/v1/cstddef),)
STDLIB_FLAGS := -stdlib=libc++ -isystem $(LLVM_PREFIX)/include/c++/v1 \
	-L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib
endif
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Werror -pedantic
BUILD_DIR ?= build
TEST_BINARY := $(BUILD_DIR)/cache_path_tests

.PHONY: all test clean

all: $(TEST_BINARY)

$(TEST_BINARY): src/cache_path.cpp tests/protected/cache_path_test.cpp include/cache_path.h
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(STDLIB_FLAGS) -Iinclude src/cache_path.cpp \
		tests/protected/cache_path_test.cpp -o $(TEST_BINARY)

test: $(TEST_BINARY)
	./$(TEST_BINARY)

clean:
	rm -rf -- $(BUILD_DIR)
