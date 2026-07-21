#include <beacon/queue.hpp>

#include <string>
#include <vector>

int main() {
    const std::vector<int> jobs{4, -2, 9};
    return beacon::summarize(jobs) == "jobs=3,total=11" ? 0 : 1;
}
