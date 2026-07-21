#include <beacon/queue.hpp>

#include <iostream>
#include <vector>

int main() {
    const std::vector<int> jobs{3, 8, -1, 9};
    std::cout << beacon::summarize(jobs) << '\n';
    return 0;
}
