CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -g

test_clinic: test_clinic.cpp clinic.cpp clinic.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_clinic.cpp clinic.cpp

.PHONY: test clean
test: test_clinic
	./test_clinic

clean:
	rm -f test_clinic
