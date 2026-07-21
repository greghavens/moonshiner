ifeq ($(origin CXX),default)
CXX := $(firstword $(foreach compiler,c++ g++ clang++,$(shell command -v $(compiler) 2>/dev/null)))
endif

ifeq ($(CXX),)
$(error A C++20 compiler is required)
endif

CPPFLAGS := -Iinclude
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror
LDFLAGS :=

STDLIB_READY := $(shell $(CXX) -std=c++20 -x c++ -include array -E /dev/null >/dev/null 2>&1 && echo yes)
ifneq ($(STDLIB_READY),yes)
LLVM_RESOURCE_DIR := $(shell $(CXX) --print-resource-dir 2>/dev/null)
LLVM_PREFIX := $(abspath $(LLVM_RESOURCE_DIR)/../../..)
ifneq ($(wildcard $(LLVM_PREFIX)/include/c++/v1/array),)
CXXFLAGS += -stdlib=libc++
LDFLAGS += -stdlib=libc++ -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib
else
$(error $(CXX) cannot find C++ standard-library headers)
endif
endif

BUILD_DIR := .build
DECODER_OBJ := $(BUILD_DIR)/decoder.o
PUBLIC_BIN := $(BUILD_DIR)/public_tests
PROTECTED_BIN := $(BUILD_DIR)/protected_tests

.PHONY: all test clean

all: $(PUBLIC_BIN) $(PROTECTED_BIN)

test: all
	$(PUBLIC_BIN)
	$(PROTECTED_BIN)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(DECODER_OBJ): src/decoder.cpp include/framebudget/decoder.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(PUBLIC_BIN): tests/public_tests.cpp tests/test_support.hpp $(DECODER_OBJ) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $< $(DECODER_OBJ) $(LDFLAGS) -o $@

$(PROTECTED_BIN): tests/protected_tests.cpp tests/test_support.hpp $(DECODER_OBJ) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $< $(DECODER_OBJ) $(LDFLAGS) -o $@

clean:
	rm -f $(DECODER_OBJ) $(PUBLIC_BIN) $(PROTECTED_BIN)
	rmdir $(BUILD_DIR) 2>/dev/null || true
