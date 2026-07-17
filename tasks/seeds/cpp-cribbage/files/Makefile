CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_cribbage: test_cribbage.cpp cribbage.cpp cribbage.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_cribbage.cpp cribbage.cpp

.PHONY: test clean
test: test_cribbage
	./test_cribbage

clean:
	rm -f test_cribbage
