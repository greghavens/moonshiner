CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_guard: test_guard.cpp guard.cpp guard.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_guard.cpp guard.cpp

.PHONY: test clean
test: test_guard
	./test_guard

clean:
	rm -f test_guard
