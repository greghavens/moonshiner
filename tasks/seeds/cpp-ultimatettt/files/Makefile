CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_uttt: test_uttt.cpp uttt.cpp uttt.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_uttt.cpp uttt.cpp

.PHONY: test clean
test: test_uttt
	./test_uttt

clean:
	rm -f test_uttt
