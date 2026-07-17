CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_eval: test_eval.cpp eval.cpp eval.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_eval.cpp eval.cpp

.PHONY: test clean
test: test_eval
	./test_eval

clean:
	rm -f test_eval
