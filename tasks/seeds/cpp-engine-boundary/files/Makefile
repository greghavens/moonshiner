CXX      = g++
CXXFLAGS = -std=c++20 -Wall -Wextra -Werror -pedantic -Igenerated -Iinclude

OBJS = simulation_core.o simulation_abi.o test_engine.o

test_engine: $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $(OBJS)

simulation_core.o: src/simulation_core.cpp include/simulation_core.hpp
	$(CXX) $(CXXFLAGS) -c src/simulation_core.cpp -o $@

simulation_abi.o: src/simulation_abi.cpp generated/simulation_bindings.h include/simulation_adapter.hpp include/simulation_core.hpp
	$(CXX) $(CXXFLAGS) -c src/simulation_abi.cpp -o $@

test_engine.o: test_engine.cpp mintest.h generated/simulation_bindings.h include/simulation_adapter.hpp include/simulation_core.hpp
	$(CXX) $(CXXFLAGS) -c test_engine.cpp -o $@

.PHONY: test clean
test: test_engine
	./test_engine

clean:
	rm -f test_engine *.o
