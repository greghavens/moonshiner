/* Acceptance tests for InlineVec<T, N> (inlinevec.hpp).
 * Build and run with `make test`.
 *
 * Contract pinned here:
 *   - all element storage lives inside the InlineVec object itself, aligned
 *     for T; an empty container constructs no T at all (T need not even be
 *     default-constructible);
 *   - push_back/emplace_back refuse politely when full (return false,
 *     construct nothing) and give the strong guarantee when an element
 *     constructor throws;
 *   - copy construction cleans up the partial copy if an element throws;
 *     copy assignment is all-or-nothing; moves transfer element-wise and
 *     leave the source empty;
 *   - live-object accounting is exact: everything constructed is destroyed
 *     exactly once.
 */
#include "mintest.h"

#include "inlinevec.hpp"

#include <algorithm>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <type_traits>
#include <utility>

/* Instrumented element type: counts constructions, copies, moves and live
 * objects, and can be told to throw once a copy budget is used up. */
struct CopyLimit {};

struct Probe {
    static int live;
    static int made;
    static int copies;
    static int moves;
    static int copy_budget; /* -1 = unlimited */

    int v;

    explicit Probe(int x) : v(x) {
        ++made;
        ++live;
    }
    Probe(const Probe &o) : v(o.v) {
        if (copy_budget >= 0 && copies >= copy_budget)
            throw CopyLimit{};
        ++copies;
        ++made;
        ++live;
    }
    Probe(Probe &&o) noexcept : v(o.v) {
        o.v = -1;
        ++moves;
        ++made;
        ++live;
    }
    Probe &operator=(const Probe &) = delete;
    Probe &operator=(Probe &&) = delete;
    ~Probe() { --live; }
};
int Probe::live = 0;
int Probe::made = 0;
int Probe::copies = 0;
int Probe::moves = 0;
int Probe::copy_budget = -1;

static void probe_reset(int budget = -1) {
    Probe::made = 0;
    Probe::copies = 0;
    Probe::moves = 0;
    Probe::copy_budget = budget;
}

static_assert(InlineVec<int, 4>::capacity() == 4,
              "capacity() is the template parameter");
static_assert(InlineVec<Probe, 3>::capacity() == 3,
              "capacity() works for non-default-constructible T");
static_assert(std::is_nothrow_move_constructible_v<InlineVec<int, 4>>,
              "move construction is noexcept for nothrow-movable T");
static_assert(std::is_nothrow_move_constructible_v<InlineVec<Probe, 4>>,
              "move construction is noexcept for nothrow-movable T");

TEST(push_back_reports_capacity_honestly) {
    InlineVec<int, 3> v;
    CHECK(v.empty(), "starts empty");
    CHECK_EQ_INT(v.size(), 0, "starts at size zero");
    CHECK(v.push_back(4), "1st push fits");
    CHECK(v.push_back(5), "2nd push fits");
    CHECK(v.push_back(6), "3rd push fits");
    CHECK(!v.push_back(7), "4th push is refused");
    CHECK_EQ_INT(v.size(), 3, "size capped at capacity");
    CHECK_EQ_INT(v[0], 4, "first value intact");
    CHECK_EQ_INT(v[1], 5, "second value intact");
    CHECK_EQ_INT(v[2], 6, "third value intact");
    CHECK(!v.empty(), "no longer empty");
}

TEST(storage_lives_inside_the_object) {
    InlineVec<double, 4> v;
    v.push_back(1.5);
    v.push_back(2.5);
    auto lo = reinterpret_cast<std::uintptr_t>(&v);
    auto hi = lo + sizeof(v);
    auto d0 = reinterpret_cast<std::uintptr_t>(v.data());
    auto d1 = reinterpret_cast<std::uintptr_t>(v.data() + decltype(v)::capacity());
    CHECK(d0 >= lo && d1 <= hi, "element storage sits inside the object footprint");
    CHECK_EQ_INT(d0 % alignof(double), 0, "storage is aligned for the element type");
    CHECK(v.data() == &v[0], "data() points at the first element");
    CHECK(v.data() + 1 == &v[1], "elements are contiguous");
    CHECK(v[0] == 1.5, "value survives through data()");
}

TEST(empty_container_constructs_no_elements) {
    probe_reset();
    {
        InlineVec<Probe, 8> v;
        CHECK_EQ_INT(Probe::made, 0, "no hidden element constructions");
        CHECK_EQ_INT(Probe::live, 0, "nothing live in an empty container");
        CHECK(v.emplace_back(1), "emplace succeeds");
        CHECK_EQ_INT(Probe::live, 1, "exactly one element live after emplace");
    }
    CHECK_EQ_INT(Probe::live, 0, "destructor destroys the live elements");
}

TEST(refusals_construct_nothing) {
    probe_reset();
    InlineVec<Probe, 2> v;
    v.emplace_back(1);
    v.emplace_back(2);
    int made_before = Probe::made;
    CHECK(!v.emplace_back(3), "emplace on a full container refuses");
    CHECK_EQ_INT(Probe::made - made_before, 0, "refused emplace constructs nothing");
    Probe extra(9);
    made_before = Probe::made;
    CHECK(!v.push_back(extra), "push on a full container refuses");
    CHECK_EQ_INT(Probe::made - made_before, 0, "refused push copies nothing");
    CHECK_EQ_INT(v.size(), 2, "size unchanged by refusals");
    CHECK_EQ_INT(v[0].v, 1, "elements unchanged by refusals");
    CHECK_EQ_INT(v[1].v, 2, "elements unchanged by refusals");
    probe_reset();
}

TEST(pop_back_and_clear_destroy_immediately) {
    probe_reset();
    InlineVec<Probe, 4> v;
    v.emplace_back(1);
    v.emplace_back(2);
    v.emplace_back(3);
    v.pop_back();
    CHECK_EQ_INT(v.size(), 2, "pop_back shrinks by one");
    CHECK_EQ_INT(Probe::live, 2, "popped element destroyed right away");
    CHECK_EQ_INT(v[1].v, 2, "remaining elements intact");
    v.clear();
    CHECK_EQ_INT(v.size(), 0, "clear empties the container");
    CHECK_EQ_INT(Probe::live, 0, "clear destroys every element");
    v.clear();
    CHECK_EQ_INT(v.size(), 0, "clear is idempotent");
    CHECK(v.emplace_back(9), "container is reusable after clear");
    CHECK_EQ_INT(v[0].v, 9, "fresh element reads back");
    probe_reset();
}

TEST(emplace_back_constructs_in_place) {
    probe_reset();
    InlineVec<Probe, 2> v;
    CHECK(v.emplace_back(41), "first emplace fits");
    CHECK(v.emplace_back(42), "second emplace fits");
    CHECK_EQ_INT(Probe::copies, 0, "emplace copies nothing");
    CHECK_EQ_INT(Probe::moves, 0, "emplace moves nothing");
    CHECK_EQ_INT(v[0].v, 41, "emplaced value");
    CHECK_EQ_INT(v[1].v, 42, "emplaced value");
    probe_reset();
}

TEST(push_back_keeps_the_container_intact_when_the_copy_throws) {
    probe_reset();
    InlineVec<Probe, 4> v;
    v.emplace_back(7);
    v.emplace_back(8);
    Probe extra(9);
    probe_reset(0); /* every copy from here on throws */
    bool threw = false;
    try {
        v.push_back(extra);
    } catch (const CopyLimit &) {
        threw = true;
    }
    CHECK(threw, "the element's exception escapes push_back");
    CHECK_EQ_INT(v.size(), 2, "size unchanged after the failed push");
    CHECK_EQ_INT(v[0].v, 7, "existing element untouched");
    CHECK_EQ_INT(v[1].v, 8, "existing element untouched");
    CHECK_EQ_INT(Probe::live, 3, "no stray live element left behind");
    probe_reset();
}

TEST(copies_are_independent) {
    probe_reset();
    InlineVec<Probe, 4> a;
    a.emplace_back(1);
    a.emplace_back(2);
    InlineVec<Probe, 4> b(a);
    CHECK_EQ_INT(b.size(), 2, "copy has the source's length");
    CHECK_EQ_INT(Probe::copies, 2, "each element copied once");
    b.pop_back();
    b.emplace_back(99);
    CHECK_EQ_INT(a[1].v, 2, "original untouched by edits to the copy");
    CHECK_EQ_INT(b[1].v, 99, "copy took the edit");
    CHECK_EQ_INT(Probe::live, 4, "two containers, two live elements each");
    probe_reset();
}

TEST(copy_construction_cleans_up_after_a_throwing_element) {
    probe_reset();
    {
        InlineVec<Probe, 6> src;
        for (int i = 0; i < 4; i++)
            src.emplace_back(i + 1);
        CHECK_EQ_INT(Probe::live, 4, "four source elements live");
        probe_reset(2); /* the third copy throws */
        bool threw = false;
        try {
            InlineVec<Probe, 6> dup(src);
            CHECK(false, "copy construction must not swallow the exception");
        } catch (const CopyLimit &) {
            threw = true;
        }
        CHECK(threw, "copy construction propagates the element's exception");
        CHECK_EQ_INT(Probe::copies, 2, "two elements copied before the throw");
        CHECK_EQ_INT(Probe::live, 4, "partially built copy fully destroyed");
        CHECK_EQ_INT(src.size(), 4, "source untouched");
    }
    CHECK_EQ_INT(Probe::live, 0, "everything destroyed at scope exit");
    probe_reset();
}

TEST(copy_assignment_is_all_or_nothing) {
    probe_reset();
    InlineVec<Probe, 4> dst;
    dst.emplace_back(10);
    dst.emplace_back(20);
    InlineVec<Probe, 4> src;
    for (int i = 0; i < 4; i++)
        src.emplace_back(i + 1);

    probe_reset(2); /* not enough budget for four element copies */
    bool threw = false;
    try {
        dst = src;
    } catch (const CopyLimit &) {
        threw = true;
    }
    CHECK(threw, "assignment propagates the element's exception");
    CHECK_EQ_INT(dst.size(), 2, "target keeps its old length");
    CHECK_EQ_INT(dst[0].v, 10, "target keeps its old values");
    CHECK_EQ_INT(dst[1].v, 20, "target keeps its old values");
    CHECK_EQ_INT(src.size(), 4, "source untouched by the failure");
    CHECK_EQ_INT(Probe::live, 6, "no leaked temporaries");

    probe_reset();
    dst = src;
    CHECK_EQ_INT(dst.size(), 4, "assignment succeeds with budget");
    for (int i = 0; i < 4; i++)
        CHECK_EQ_INT(dst[i].v, i + 1, "assigned value matches the source");
    CHECK_EQ_INT(Probe::live, 8, "both containers fully populated, no leaks");
    probe_reset();
}

TEST(self_assignment_changes_nothing) {
    probe_reset();
    InlineVec<Probe, 3> v;
    v.emplace_back(5);
    v.emplace_back(6);
    InlineVec<Probe, 3> *alias = &v;
    v = *alias;
    CHECK_EQ_INT(v.size(), 2, "size unchanged by self-assignment");
    CHECK_EQ_INT(v[0].v, 5, "values unchanged by self-assignment");
    CHECK_EQ_INT(v[1].v, 6, "values unchanged by self-assignment");
    CHECK_EQ_INT(Probe::live, 2, "no live-count drift");
    probe_reset();
}

TEST(move_construction_transfers_and_empties_the_source) {
    probe_reset();
    InlineVec<Probe, 4> src;
    src.emplace_back(1);
    src.emplace_back(2);
    src.emplace_back(3);
    probe_reset();
    InlineVec<Probe, 4> dst(std::move(src));
    CHECK_EQ_INT(dst.size(), 3, "target took the elements");
    CHECK_EQ_INT(dst[0].v, 1, "moved value");
    CHECK_EQ_INT(dst[1].v, 2, "moved value");
    CHECK_EQ_INT(dst[2].v, 3, "moved value");
    CHECK(src.empty(), "source is empty after the move");
    CHECK_EQ_INT(Probe::copies, 0, "moving copies nothing");
    CHECK_EQ_INT(Probe::moves, 3, "elements are moved one by one");
    CHECK_EQ_INT(Probe::live, 3, "the source's elements were destroyed");
    probe_reset();
}

TEST(move_assignment_replaces_the_target) {
    probe_reset();
    InlineVec<Probe, 4> dst;
    dst.emplace_back(9);
    InlineVec<Probe, 4> src;
    src.emplace_back(5);
    src.emplace_back(6);
    dst = std::move(src);
    CHECK_EQ_INT(dst.size(), 2, "target took the source's length");
    CHECK_EQ_INT(dst[0].v, 5, "moved value");
    CHECK_EQ_INT(dst[1].v, 6, "moved value");
    CHECK(src.empty(), "source is empty after move-assignment");
    CHECK_EQ_INT(Probe::live, 2, "old target element destroyed, no leaks");
    probe_reset();
}

TEST(iterates_with_range_for_and_algorithms) {
    InlineVec<int, 5> v;
    for (int x : {3, 1, 4, 1, 5})
        CHECK(v.push_back(x), "seed value fits");
    long sum = 0;
    for (int x : v)
        sum += x;
    CHECK_EQ_INT(sum, 14, "range-for visits every element once");
    CHECK_EQ_INT(v.end() - v.begin(), 5, "begin/end span exactly the size");
    const auto &cv = v;
    CHECK_EQ_INT(cv.end() - cv.begin(), 5, "const iteration works");
    CHECK_EQ_INT(*std::max_element(cv.begin(), cv.end()), 5,
                 "plays nicely with <algorithm>");
}

TEST(holds_owning_types_like_strings) {
    const std::string wa(40, 'a');
    const std::string wb(40, 'b');
    InlineVec<std::string, 3> names;
    CHECK(names.push_back(std::string(40, 'a')), "long string fits");
    CHECK(names.emplace_back(40, 'b'), "emplace forwards ctor arguments");
    InlineVec<std::string, 3> dup(names);
    names.pop_back();
    CHECK_EQ_INT(dup.size(), 2, "copy keeps its own length");
    CHECK_EQ_STR(dup[0].c_str(), wa.c_str(), "deep copy of element 0");
    CHECK_EQ_STR(dup[1].c_str(), wb.c_str(), "deep copy of element 1");
    InlineVec<std::string, 3> moved(std::move(dup));
    CHECK_EQ_INT(moved.size(), 2, "move carries the elements");
    CHECK(dup.empty(), "moved-from container is empty");
    CHECK_EQ_STR(moved[0].c_str(), wa.c_str(), "moved element 0 intact");
    CHECK_EQ_STR(moved[1].c_str(), wb.c_str(), "moved element 1 intact");
    CHECK(!moved.push_back("x") || moved.size() == 3, "capacity math still applies");
}

int main(void) {
    RUN(push_back_reports_capacity_honestly);
    RUN(storage_lives_inside_the_object);
    RUN(empty_container_constructs_no_elements);
    RUN(refusals_construct_nothing);
    RUN(pop_back_and_clear_destroy_immediately);
    RUN(emplace_back_constructs_in_place);
    RUN(push_back_keeps_the_container_intact_when_the_copy_throws);
    RUN(copies_are_independent);
    RUN(copy_construction_cleans_up_after_a_throwing_element);
    RUN(copy_assignment_is_all_or_nothing);
    RUN(self_assignment_changes_nothing);
    RUN(move_construction_transfers_and_empties_the_source);
    RUN(move_assignment_replaces_the_target);
    RUN(iterates_with_range_for_and_algorithms);
    RUN(holds_owning_types_like_strings);
    return mt_summary();
}
