CXX ?= g++
CPPFLAGS := -Iinclude
CXXFLAGS ?= -std=c++20 -O2 -g -Wall -Wextra -Wpedantic

SOURCES := src/event_loop.cpp src/session.cpp src/runtime.cpp

.PHONY: all test clean

all: shutdown_demo

shutdown_demo: $(SOURCES) src/main.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $^ -o $@

test:
	bash protected_tests/verify.sh

clean:
	rm -f shutdown_demo
