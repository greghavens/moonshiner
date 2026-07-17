CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_refcard: test_refcard.cpp refcard.cpp refcard.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_refcard.cpp refcard.cpp

.PHONY: test clean
test: test_refcard
	./test_refcard

clean:
	rm -f test_refcard
