CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_medals: test_medals.cpp medals.h board.h fmt.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_medals.cpp

.PHONY: test clean
test: test_medals
	./test_medals

clean:
	rm -f test_medals
