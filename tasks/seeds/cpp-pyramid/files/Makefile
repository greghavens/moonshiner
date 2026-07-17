CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_pyramid: test_pyramid.cpp pyramid.cpp pyramid.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_pyramid.cpp pyramid.cpp

.PHONY: test clean
test: test_pyramid
	./test_pyramid

clean:
	rm -f test_pyramid
