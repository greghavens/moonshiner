#include "beacon/queue.hpp"

#include <numeric>
#include <thread>

namespace beacon {

std::string summarize(const std::vector<int>& jobs) {
    int total = 0;
    std::thread worker([&jobs, &total] {
        total = std::accumulate(jobs.begin(), jobs.end(), 0);
    });
    worker.join();
    return "jobs=" + std::to_string(jobs.size()) + ",total=" +
           std::to_string(total);
}

}  // namespace beacon
