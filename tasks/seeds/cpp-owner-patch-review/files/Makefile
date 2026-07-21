CC = clang
CXX = clang++
LLVM_PREFIX = $(abspath $(shell $(CXX) --print-resource-dir)/../../..)
CPPFLAGS = -Iinclude
CFLAGS = -std=c11 -O2 -Wall -Wextra -Werror -pedantic
CXXFLAGS = -std=c++20 -stdlib=libc++ -O2 -Wall -Wextra -Werror -pedantic
LDFLAGS = -stdlib=libc++ -L$(LLVM_PREFIX)/lib -Wl,-rpath,$(LLVM_PREFIX)/lib

OWNER_OBJECT = build/owner_api.o
CPP_TEST_OBJECT = build/test_owner.o
C_TEST_OBJECT = build/c_abi_smoke.o
CPP_TEST = build/test_owner
C_TEST = build/c_abi_smoke

.PHONY: all test clean

all: $(CPP_TEST) $(C_TEST)

build:
	mkdir -p build

$(OWNER_OBJECT): src/owner_api.cpp include/owner_api.h | build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(CPP_TEST_OBJECT): protected_tests/test_owner.cpp include/owner_api.h include/owner_adapter.hpp | build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(C_TEST_OBJECT): protected_tests/c_abi_smoke.c include/owner_api.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(CPP_TEST): $(OWNER_OBJECT) $(CPP_TEST_OBJECT)
	$(CXX) $(LDFLAGS) $^ -o $@

$(C_TEST): $(OWNER_OBJECT) $(C_TEST_OBJECT)
	$(CXX) $(LDFLAGS) $^ -o $@

test: all
	./$(CPP_TEST)
	./$(C_TEST)

clean:
	rm -rf build
