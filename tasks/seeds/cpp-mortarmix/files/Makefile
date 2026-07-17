CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -Wconversion -Wsign-conversion

test_mix: mix.o test_mix.o
	$(CXX) $(CXXFLAGS) -o $@ mix.o test_mix.o

mix.o: mix.cpp mix.h
	$(CXX) $(CXXFLAGS) -c mix.cpp

test_mix.o: test_mix.cpp mix.h mintest.h
	$(CXX) $(CXXFLAGS) -c test_mix.cpp

.PHONY: test clean
test: test_mix
	./test_mix

clean:
	rm -f test_mix *.o
