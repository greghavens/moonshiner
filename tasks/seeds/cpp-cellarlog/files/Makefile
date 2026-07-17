CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_cellar: cellar.o report.o test_cellar.o
	$(CXX) $(CXXFLAGS) -o $@ cellar.o report.o test_cellar.o

cellar.o: cellar.cpp cellar.h bin.h rack.h
	$(CXX) $(CXXFLAGS) -c cellar.cpp

report.o: report.cpp report.h rack.h bin.h
	$(CXX) $(CXXFLAGS) -c report.cpp

test_cellar.o: test_cellar.cpp cellar.h report.h bin.h rack.h mintest.h
	$(CXX) $(CXXFLAGS) -c test_cellar.cpp

.PHONY: test clean
test: test_cellar
	./test_cellar

clean:
	rm -f test_cellar *.o
