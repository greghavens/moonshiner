#ifndef SPATIAL_PROXIMITY_HPP
#define SPATIAL_PROXIMITY_HPP

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace spatial {

struct Point {
    double x;
    double y;
};

struct Proximity {
    std::size_t first;
    std::size_t second;
    double distance;
};

struct ScanStats {
    std::uint64_t distance_comparisons = 0;
};

// Finds all unordered pairs at or below max_distance. Results are ordered by
// (first, second). When supplied, stats is reset for each call.
std::vector<Proximity> scan_proximity(
    std::span<const Point> points,
    double max_distance,
    ScanStats* stats = nullptr);

}  // namespace spatial

#endif
