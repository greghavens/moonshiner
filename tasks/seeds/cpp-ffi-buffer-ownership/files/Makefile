CXX := clang++
LLVM_PREFIX := $(abspath $(dir $(shell command -v $(CXX)))/..)
CXXFLAGS := -std=c++17 -stdlib=libc++ -O2 -Wall -Wextra -Werror -pedantic
CPPFLAGS := -Iinclude
LDFLAGS := -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib

.PHONY: all test clean

all: build/test_buffer_transfer

build/test_buffer_transfer: src/buffer_transfer.cpp tests/test_buffer_transfer.cpp include/buffer_transfer.h
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/buffer_transfer.cpp tests/test_buffer_transfer.cpp $(LDFLAGS) -o $@

test: build/test_buffer_transfer
	./build/test_buffer_transfer

clean:
	rm -rf build
