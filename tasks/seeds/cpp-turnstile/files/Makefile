CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror

test_lanes: test_lanes.cpp lane.h card_lane.h bank.h mintest.h
	$(CXX) $(CXXFLAGS) -o $@ test_lanes.cpp

.PHONY: test clean
test: test_lanes
	./test_lanes

clean:
	rm -f test_lanes
