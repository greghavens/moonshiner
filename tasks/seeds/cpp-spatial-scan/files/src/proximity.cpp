#include "spatial/proximity.hpp"

#include <cmath>
#include <stdexcept>
#include <vector>

namespace spatial {

std::vector<Proximity> scan_proximity(
    std::span<const Point> points,
    double max_distance,
    ScanStats* stats) {
    if (stats != nullptr) {
        stats->distance_comparisons = 0;
    }
    if (!std::isfinite(max_distance) || max_distance < 0.0) {
        throw std::invalid_argument("max_distance must be finite and non-negative");
    }
    for (const Point& point : points) {
        if (!std::isfinite(point.x) || !std::isfinite(point.y)) {
            throw std::invalid_argument("point coordinates must be finite");
        }
    }

    std::vector<Proximity> matches;
    for (std::size_t first = 0; first < points.size(); ++first) {
        for (std::size_t second = first + 1; second < points.size(); ++second) {
            if (stats != nullptr) {
                ++stats->distance_comparisons;
            }
            const double distance = std::hypot(
                points[first].x - points[second].x,
                points[first].y - points[second].y);
            if (distance <= max_distance) {
                matches.push_back(Proximity{first, second, distance});
            }
        }
    }
    return matches;
}

}  // namespace spatial
