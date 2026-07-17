CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_vat: test_vat.cpp vatstats.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_vat.cpp

.PHONY: test clean
test: test_vat
	./test_vat

clean:
	rm -f test_vat
