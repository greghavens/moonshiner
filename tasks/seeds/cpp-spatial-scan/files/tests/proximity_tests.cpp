#include "spatial/proximity.hpp"

#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <iostream>
#include <limits>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using spatial::Point;
using spatial::Proximity;
using spatial::ScanStats;

int failures = 0;

void check(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        ++failures;
    }
}

std::vector<Proximity> oracle(std::span<const Point> points, double radius) {
    std::vector<Proximity> expected;
    for (std::size_t first = 0; first < points.size(); ++first) {
        for (std::size_t second = first + 1; second < points.size(); ++second) {
            const double distance = std::hypot(
                points[first].x - points[second].x,
                points[first].y - points[second].y);
            if (distance <= radius) {
                expected.push_back(Proximity{first, second, distance});
            }
        }
    }
    return expected;
}

void check_matches(
    const std::vector<Proximity>& actual,
    const std::vector<Proximity>& expected,
    const std::string& context) {
    check(actual.size() == expected.size(), context + ": result count");
    const std::size_t count = actual.size() < expected.size() ? actual.size() : expected.size();
    for (std::size_t index = 0; index < count; ++index) {
        check(
            actual[index].first == expected[index].first
                && actual[index].second == expected[index].second,
            context + ": deterministic pair order at result " + std::to_string(index));
        check(
            std::bit_cast<std::uint64_t>(actual[index].distance)
                == std::bit_cast<std::uint64_t>(expected[index].distance),
            context + ": exact distance at result " + std::to_string(index));
    }
}

void test_small_input_contract() {
    const std::vector<Point> points{
        {0.0, 0.0},
        {3.0, 4.0},
        {6.0, 8.0},
        {3.0, 0.0},
        {0.0, 4.0},
    };
    ScanStats stats{999};
    const auto actual = spatial::scan_proximity(points, 5.0, &stats);
    check_matches(actual, oracle(points, 5.0), "small input");
    check(stats.distance_comparisons == 10, "small input keeps all-pairs comparison behavior");

    const std::vector<Point> duplicates{{2.5, -4.0}, {0.0, 0.0}, {2.5, -4.0}};
    const auto zero_radius = spatial::scan_proximity(duplicates, 0.0, &stats);
    check_matches(zero_radius, oracle(duplicates, 0.0), "zero radius");
    check(stats.distance_comparisons == 3, "zero-radius small scan comparison count");

    const std::vector<Point> no_points;
    spatial::scan_proximity(no_points, 1.0, &stats);
    check(stats.distance_comparisons == 0, "empty scan resets stats");

    std::vector<Point> cutoff_points;
    for (std::size_t index = 0; index < 32; ++index) {
        cutoff_points.push_back(Point{
            static_cast<double>(index) * 3.0,
            static_cast<double>(index) * 7.0,
        });
    }
    const auto cutoff = spatial::scan_proximity(cutoff_points, 0.5, &stats);
    check_matches(cutoff, oracle(cutoff_points, 0.5), "32-point cutoff");
    check(
        stats.distance_comparisons == 32 * 31 / 2,
        "inputs through 32 points retain exact all-pairs comparison counts");
}

void test_validation_contract() {
    ScanStats stats{77};
    const double invalid_radii[]{
        -1.0,
        std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::quiet_NaN(),
    };
    for (double radius : invalid_radii) {
        stats.distance_comparisons = 77;
        bool rejected_radius = false;
        try {
            const std::vector<Point> points{{0.0, 0.0}};
            static_cast<void>(spatial::scan_proximity(points, radius, &stats));
        } catch (const std::invalid_argument&) {
            rejected_radius = true;
        }
        check(rejected_radius, "each invalid radius is rejected");
        check(stats.distance_comparisons == 0, "stats reset before radius validation");
    }

    const std::vector<Point> invalid_points{
        {std::numeric_limits<double>::infinity(), 0.0},
        {-std::numeric_limits<double>::infinity(), 0.0},
        {std::numeric_limits<double>::quiet_NaN(), 0.0},
        {0.0, std::numeric_limits<double>::quiet_NaN()},
    };
    for (const Point point : invalid_points) {
        stats.distance_comparisons = 88;
        bool rejected_coordinate = false;
        try {
            const std::vector<Point> points{{0.0, 0.0}, point};
            static_cast<void>(spatial::scan_proximity(points, 1.0, &stats));
        } catch (const std::invalid_argument&) {
            rejected_coordinate = true;
        }
        check(rejected_coordinate, "each non-finite coordinate is rejected");
        check(stats.distance_comparisons == 0, "stats reset before point validation");
    }
}

void test_large_result_contract() {
    const double just_over_five = std::nextafter(
        5.0, std::numeric_limits<double>::infinity());
    std::vector<Point> boundary_points{
        {5.0, 0.0},
        {0.0, 0.0},
        {3.0, 4.0},
        {3.0, -4.0},
        {-3.0, 4.0},
        {-3.0, -4.0},
        {just_over_five, 0.0},
        {0.0, just_over_five},
        {5.0, 0.0},
    };
    for (std::size_t index = boundary_points.size(); index < 48; ++index) {
        boundary_points.push_back(Point{
            1000.0 + static_cast<double>(index) * 20.0,
            -2000.0 - static_cast<double>(index) * 30.0,
        });
    }

    ScanStats stats;
    const auto boundary_actual = spatial::scan_proximity(boundary_points, 5.0, &stats);
    check_matches(boundary_actual, oracle(boundary_points, 5.0), "large boundary scan");

    const std::vector<Point> duplicates(40, Point{2.5, -4.0});
    const auto dense_actual = spatial::scan_proximity(duplicates, -0.0, &stats);
    check_matches(dense_actual, oracle(duplicates, 0.0), "large dense zero-radius scan");
    check(
        stats.distance_comparisons == duplicates.size() * (duplicates.size() - 1) / 2,
        "dense candidates count every exact distance evaluation");

    std::vector<Point> tiny_separations;
    for (std::size_t index = 0; index < 40; ++index) {
        tiny_separations.push_back(Point{
            static_cast<double>(index) * 1.0e-200,
            0.0,
        });
    }
    const auto tiny_actual = spatial::scan_proximity(tiny_separations, 0.0, &stats);
    check_matches(tiny_actual, oracle(tiny_separations, 0.0), "underflow-scale scan");

    std::vector<Point> huge_coordinates{{0.0, 0.0}, {1.0e200, 0.0}};
    for (std::size_t index = 2; index < 40; ++index) {
        huge_coordinates.push_back(Point{
            1.0e300,
            -1.0e300 + static_cast<double>(index) * 1.0e286,
        });
    }
    const auto huge_actual = spatial::scan_proximity(huge_coordinates, 1.0e200, &stats);
    check_matches(huge_actual, oracle(huge_coordinates, 1.0e200), "overflow-scale scan");
}

std::vector<Point> large_fixture(std::size_t columns) {
    std::vector<Point> points{
        {0.25, 0.25},
        {0.0, 0.0},
        {0.2, 0.1},
        {0.1, 0.2},
    };
    for (std::size_t column = 0; column < columns; ++column) {
        for (std::size_t row = 0; row < 8; ++row) {
            points.push_back(Point{
                100.0 + static_cast<double>(column) * 10.0,
                100.0 + static_cast<double>(row) * 10.0,
            });
        }
    }
    return points;
}

void test_large_sparse_scan() {
    const auto smaller = large_fixture(32);
    const auto larger = large_fixture(64);
    ScanStats smaller_stats;
    ScanStats larger_stats;

    const auto smaller_actual = spatial::scan_proximity(smaller, 1.0, &smaller_stats);
    const auto larger_actual = spatial::scan_proximity(larger, 1.0, &larger_stats);
    check_matches(smaller_actual, oracle(smaller, 1.0), "optimized smaller scan");
    check_matches(larger_actual, oracle(larger, 1.0), "optimized larger scan");

    check(
        larger_stats.distance_comparisons <= 8 * larger.size(),
        "comparison-count harness rejects a quadratic sparse scan (observed "
            + std::to_string(larger_stats.distance_comparisons) + ")");
    check(
        larger_stats.distance_comparisons
            <= 3 * smaller_stats.distance_comparisons + 32,
        "doubling sparse input must not quadruple distance comparisons");

    std::vector<Point> vertical_points;
    for (std::size_t index = 0; index < 600; ++index) {
        vertical_points.push_back(Point{
            42.0,
            static_cast<double>(index) * 10.0,
        });
    }
    ScanStats vertical_stats;
    const auto vertical_actual = spatial::scan_proximity(vertical_points, 1.0, &vertical_stats);
    check_matches(vertical_actual, oracle(vertical_points, 1.0), "same-x sparse scan");
    check(
        vertical_stats.distance_comparisons <= 2 * vertical_points.size(),
        "sparse selection must use the y dimension (observed "
            + std::to_string(vertical_stats.distance_comparisons) + ")");
}

}  // namespace

int main() {
    test_small_input_contract();
    test_validation_contract();
    test_large_result_contract();
    test_large_sparse_scan();

    if (failures != 0) {
        std::cerr << failures << " protected assertion(s) failed\n";
        return 1;
    }
    std::cout << "all protected proximity tests passed\n";
    return 0;
}
