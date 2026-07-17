/* Acceptance tests for FilterView / filtered() (filterview.hpp).
 * Build and run with `make test`.
 *
 * Invariants pinned here:
 *   - the view's iterator models std::forward_iterator (static_asserts);
 *   - dereferencing yields references to the ORIGINAL elements — the view
 *     never copies, never allocates, never touches the source;
 *   - begin() skips leading misses, ++ lands on the next match or end(),
 *     post-increment returns the pre-step position;
 *   - views are multi-pass and const-iterable: begin()/end()/empty() are
 *     const and repeatable, iterator copies advance independently;
 *   - vector and list sources work, over full ranges and subranges;
 *   - filtered(container, pred) works directly in a range-for, and the
 *     iterators compose with std::distance / accumulate / find_if.
 */
#include "mintest.h"

#include "filterview.hpp"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <list>
#include <numeric>
#include <string>
#include <type_traits>
#include <vector>

#define CHECK_STR(got_str, want, msg) do {                                  \
        const std::string mt_line = (got_str);                              \
        CHECK_EQ_STR(mt_line.c_str(), (want), (msg));                       \
    } while (0)

static bool is_even(const int &x) {
    return x % 2 == 0;
}

using SrcIt = std::vector<int>::const_iterator;
using EvenPred = bool (*)(const int &);
using EvenView = FilterView<SrcIt, EvenPred>;
using EvenIter = EvenView::iterator;

/* The point of the exercise: a real C++20 forward iterator. */
static_assert(std::forward_iterator<EvenIter>);
static_assert(std::sentinel_for<EvenIter, EvenIter>);
static_assert(std::is_same_v<EvenIter::iterator_category,
                             std::forward_iterator_tag>);
static_assert(std::is_same_v<EvenIter::difference_type, std::ptrdiff_t>);
static_assert(std::is_same_v<std::iter_value_t<EvenIter>, int>);
static_assert(std::is_same_v<std::iter_reference_t<EvenIter>, const int &>);

template <class View>
static std::string collect(const View &view) {
    std::string out;
    for (const int &x : view) {
        out += std::to_string(x);
        out += ",";
    }
    return out;
}

TEST(range_for_visits_only_matching_elements) {
    const std::vector<int> mixed = {7, 2, 9, 4, 4, 1, 8};
    FilterView view(mixed.begin(), mixed.end(), &is_even);
    CHECK_STR(collect(view), "2,4,4,8,", "evens in source order");
    CHECK(!view.empty(), "something matched");
}

TEST(boundary_elements_can_match) {
    const std::vector<int> both_ends = {2, 9, 7, 4};
    EvenView view(both_ends.begin(), both_ends.end(), &is_even);
    CHECK_STR(collect(view), "2,4,", "first and last elements kept");

    const std::vector<int> middle = {1, 2, 3};
    EvenView mid(middle.begin(), middle.end(), &is_even);
    CHECK_STR(collect(mid), "2,", "leading and trailing misses skipped");
}

TEST(no_match_means_empty_view) {
    const std::vector<int> odds = {1, 3, 5, 7};
    EvenView view(odds.begin(), odds.end(), &is_even);
    CHECK(view.begin() == view.end(), "begin reaches end when nothing matches");
    CHECK(view.empty(), "empty() agrees");
    int visited = 0;
    for (const int &x : view) {
        (void)x;
        visited++;
    }
    CHECK_EQ_INT(visited, 0, "range-for body never runs");
}

TEST(everything_matching_passes_straight_through) {
    const std::vector<int> evens = {2, 4, 6};
    EvenView view(evens.begin(), evens.end(), &is_even);
    CHECK_STR(collect(view), "2,4,6,", "all elements visited");
    CHECK_EQ_INT(std::distance(view.begin(), view.end()), 3,
                 "distance sees all three");
}

TEST(empty_source_is_an_empty_view) {
    const std::vector<int> none;
    EvenView view(none.begin(), none.end(), &is_even);
    CHECK(view.begin() == view.end(), "no elements at all");
    CHECK(view.empty(), "empty() on an empty source");
}

TEST(dereference_refers_to_the_original_element) {
    const std::vector<int> depths = {5, 2, 9, 6};
    EvenView view(depths.begin(), depths.end(), &is_even);
    auto it = view.begin();
    CHECK(&*it == &depths[1], "begin() points into the source, not a copy");
    ++it;
    CHECK(&*it == &depths[3], "increment lands on the next original element");
}

TEST(views_can_cover_a_subrange) {
    const std::vector<int> mixed = {2, 4, 5, 6, 7, 8};
    EvenView tail(mixed.begin() + 2, mixed.end(), &is_even);
    CHECK_STR(collect(tail), "6,8,", "view starts where told");
    EvenView front(mixed.begin(), mixed.begin() + 3, &is_even);
    CHECK_STR(collect(front), "2,4,", "view stops at the given end");
}

TEST(iteration_is_multi_pass) {
    const std::vector<int> mixed = {1, 2, 3, 4, 5, 6};
    EvenView view(mixed.begin(), mixed.end(), &is_even);
    CHECK_STR(collect(view), "2,4,6,", "first pass");
    CHECK_STR(collect(view), "2,4,6,", "second pass over the same view");

    auto a = view.begin();
    auto b = a; /* copies advance independently */
    ++b;
    CHECK_EQ_INT(*a, 2, "original copy unmoved");
    CHECK_EQ_INT(*b, 4, "advanced copy on the next match");
    CHECK(a != b, "the two positions differ");
    ++a;
    CHECK(a == b, "advancing the original catches up");
}

TEST(post_increment_returns_the_previous_position) {
    const std::vector<int> mixed = {1, 2, 3, 4};
    EvenView view(mixed.begin(), mixed.end(), &is_even);
    auto it = view.begin();
    auto old = it++;
    CHECK_EQ_INT(*old, 2, "returned iterator still at the old match");
    CHECK_EQ_INT(*it, 4, "the view iterator moved on");
    CHECK(old == view.begin(), "old position equals a fresh begin()");
}

TEST(default_constructed_iterators_compare_equal) {
    EvenIter d1;
    EvenIter d2;
    CHECK(d1 == d2, "two default-constructed iterators are equal");
    CHECK(!(d1 != d2), "and not unequal");
}

struct Reading {
    std::string sensor;
    int value;
    bool flagged;
};

static const std::vector<Reading> &tank_readings() {
    static const std::vector<Reading> r = {
        {"tank-a/temp", 78, false},
        {"tank-b/ph", 9, true},
        {"tank-a/ph", 7, false},
        {"tank-c/temp", 91, true},
        {"tank-c/o2", 4, true},
    };
    return r;
}

TEST(arrow_operator_reaches_element_members) {
    const auto &readings = tank_readings();
    auto flagged =
        filtered(readings, [](const Reading &r) { return r.flagged; });
    auto it = flagged.begin();
    CHECK_STR(it->sensor, "tank-b/ph", "first flagged sensor");
    CHECK_EQ_INT(it->value, 9, "its reading");
    ++it;
    CHECK_STR(it->sensor, "tank-c/temp", "second flagged sensor");
}

TEST(standard_algorithms_accept_the_view) {
    const std::vector<int> mixed = {1, 2, 3, 4, 5, 6, 7, 8};
    EvenView view(mixed.begin(), mixed.end(), &is_even);
    CHECK_EQ_INT(std::distance(view.begin(), view.end()), 4,
                 "distance counts matches");
    CHECK_EQ_INT(std::accumulate(view.begin(), view.end(), 0), 20,
                 "accumulate sums matches");

    const auto &readings = tank_readings();
    auto flagged =
        filtered(readings, [](const Reading &r) { return r.flagged; });
    auto hot = std::find_if(flagged.begin(), flagged.end(),
                            [](const Reading &r) { return r.value > 50; });
    CHECK(hot != flagged.end(), "find_if locates a flagged hot reading");
    CHECK_STR(hot->sensor, "tank-c/temp", "and it is the right one");
}

TEST(list_sources_work_too) {
    const std::list<int> feed = {11, 14, 17, 20, 23};
    FilterView view(feed.begin(), feed.end(), &is_even);
    CHECK_STR(collect(view), "14,20,", "filters a std::list");
    CHECK_EQ_INT(std::distance(view.begin(), view.end()), 2,
                 "distance over the list view");
}

TEST(filtered_helper_and_range_for_over_a_temporary) {
    const auto &readings = tank_readings();
    std::string names;
    for (const Reading &r :
         filtered(readings, [](const Reading &x) { return !x.flagged; })) {
        names += r.sensor;
        names += ",";
    }
    CHECK_STR(names, "tank-a/temp,tank-a/ph,", "quiet sensors in order");

    int flagged_count = 0;
    for (const Reading &r :
         filtered(readings, [](const Reading &x) { return x.flagged; })) {
        (void)r;
        flagged_count++;
    }
    CHECK_EQ_INT(flagged_count, 3, "three flagged readings");
}

TEST(const_views_are_iterable) {
    const std::vector<int> mixed = {3, 6, 9, 12};
    const EvenView view(mixed.begin(), mixed.end(), &is_even);
    CHECK_STR(collect(view), "6,12,", "begin()/end() callable on a const view");
    CHECK(!view.empty(), "empty() callable on a const view");
}

int main(void) {
    RUN(range_for_visits_only_matching_elements);
    RUN(boundary_elements_can_match);
    RUN(no_match_means_empty_view);
    RUN(everything_matching_passes_straight_through);
    RUN(empty_source_is_an_empty_view);
    RUN(dereference_refers_to_the_original_element);
    RUN(views_can_cover_a_subrange);
    RUN(iteration_is_multi_pass);
    RUN(post_increment_returns_the_previous_position);
    RUN(default_constructed_iterators_compare_equal);
    RUN(arrow_operator_reaches_element_members);
    RUN(standard_algorithms_accept_the_view);
    RUN(list_sources_work_too);
    RUN(filtered_helper_and_range_for_over_a_temporary);
    RUN(const_views_are_iterable);
    return mt_summary();
}
