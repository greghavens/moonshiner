CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_inlinevec: test_inlinevec.cpp inlinevec.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_inlinevec.cpp

.PHONY: test clean
test: test_inlinevec
	./test_inlinevec

clean:
	rm -f test_inlinevec
