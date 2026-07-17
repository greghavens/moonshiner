CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_report: test_report.cpp report.cpp report.hpp mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_report.cpp report.cpp

.PHONY: test clean
test: test_report
	./test_report

clean:
	rm -f test_report
