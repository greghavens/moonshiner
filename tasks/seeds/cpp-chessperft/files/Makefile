CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_chess: test_chess.cpp chess.cpp chess.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_chess.cpp chess.cpp

.PHONY: test clean
test: test_chess
	./test_chess

clean:
	rm -f test_chess
