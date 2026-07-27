ifeq ($(origin CXX),default)
CXX := $(firstword $(foreach compiler,c++ g++ clang++,$(shell command -v $(compiler) 2>/dev/null)))
endif

ifeq ($(CXX),)
$(error A C++20 compiler is required)
endif

CPPFLAGS := -Iinclude -Isrc
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror
LDFLAGS :=

STDLIB_READY := $(shell $(CXX) -std=c++20 -x c++ -include optional -E /dev/null >/dev/null 2>&1 && echo yes)
ifneq ($(STDLIB_READY),yes)
LLVM_RESOURCE_DIR := $(shell $(CXX) --print-resource-dir 2>/dev/null)
LLVM_PREFIX := $(abspath $(LLVM_RESOURCE_DIR)/../../..)
ifneq ($(wildcard $(LLVM_PREFIX)/include/c++/v1/optional),)
CXXFLAGS += -stdlib=libc++
LDFLAGS += -stdlib=libc++ -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib
else
$(error $(CXX) cannot find C++ standard-library headers)
endif
endif

BUILD_DIR := .build
PAGE_PLAN_OBJ := $(BUILD_DIR)/page_plan.o
BOUNDARY_BIN := $(BUILD_DIR)/boundary_test
PROTECTED_BIN := $(BUILD_DIR)/protected_oracle_test

.PHONY: all test clean

all: $(BOUNDARY_BIN) $(PROTECTED_BIN)

test: all
	$(BOUNDARY_BIN)
	$(PROTECTED_BIN)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(PAGE_PLAN_OBJ): src/page_plan.cpp src/page_math.hpp include/pageplan/page_plan.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(BOUNDARY_BIN): tests/boundary_test.cpp tests/test_support.hpp src/page_math.hpp $(PAGE_PLAN_OBJ) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $< $(PAGE_PLAN_OBJ) $(LDFLAGS) -o $@

$(PROTECTED_BIN): tests/protected_oracle_test.cpp tests/test_support.hpp $(PAGE_PLAN_OBJ) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $< $(PAGE_PLAN_OBJ) $(LDFLAGS) -o $@

clean:
	rm -f $(PAGE_PLAN_OBJ) $(BOUNDARY_BIN) $(PROTECTED_BIN)
	rmdir $(BUILD_DIR) 2>/dev/null || true
