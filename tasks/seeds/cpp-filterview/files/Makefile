CXX = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_filterview: test_filterview.cpp filterview.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_filterview.cpp

.PHONY: test clean
test: test_filterview
	./test_filterview

clean:
	rm -f test_filterview
