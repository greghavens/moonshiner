CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -Wdeprecated-copy-dtor

test_quilt: test_quilt.cpp quilt.h pattern.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_quilt.cpp

.PHONY: test clean
test: test_quilt
	./test_quilt

clean:
	rm -f test_quilt
