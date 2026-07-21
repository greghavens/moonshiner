CXX := clang++
LLVM_PREFIX := $(abspath $(dir $(shell command -v $(CXX)))/..)
CPPFLAGS := -Iinclude
CXXFLAGS := -std=c++17 -stdlib=libc++ -Wall -Wextra -Werror -pedantic -O2 -fno-exceptions
LDFLAGS := -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib

BUILD_DIR := .build
TEST_BIN := $(BUILD_DIR)/test_controller
SOURCES := src/controller.cpp tests/test_controller.cpp
HEADERS := include/controller.hpp

.PHONY: all build test clean

all: build

build: $(TEST_BIN)

$(TEST_BIN): $(SOURCES) $(HEADERS) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SOURCES) $(LDFLAGS) -o $@

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

test: $(TEST_BIN)
	./$(TEST_BIN)

clean:
	rm -rf $(BUILD_DIR)
