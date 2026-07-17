CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_gomoku: test_gomoku.cpp gomoku.cpp gomoku.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_gomoku.cpp gomoku.cpp

.PHONY: test clean
test: test_gomoku
	./test_gomoku

clean:
	rm -f test_gomoku
