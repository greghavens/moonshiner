CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -Wnon-virtual-dtor \
           -Woverloaded-virtual -Wshadow

test_rides: test_rides.cpp ride.h rides.h park.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_rides.cpp

.PHONY: test clean
test: test_rides
	./test_rides

clean:
	rm -f test_rides
