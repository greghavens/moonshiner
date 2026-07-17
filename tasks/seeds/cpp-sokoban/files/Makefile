CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -O2

test_sokoban: test_sokoban.cpp sokoban.cpp sokoban.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_sokoban.cpp sokoban.cpp

.PHONY: test clean
test: test_sokoban
	./test_sokoban

clean:
	rm -f test_sokoban
