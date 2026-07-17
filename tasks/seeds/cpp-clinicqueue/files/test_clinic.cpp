/* Acceptance tests for the walk-in waitlist (clinic.hpp/clinic.cpp).
 * Build and run with `make test`.
 *
 * Invariants pinned here:
 *   - after any sweep, exactly the visits whose hold_until is at or before
 *     `now` are gone — no lapsed visit survives, no live visit disappears;
 *   - desk lookups by ticket always return the right patient (or nullptr
 *     for tickets that are no longer on the board), before and after any
 *     number of sweeps and later arrivals;
 *   - sweep return values and dropped_total() agree with what actually
 *     left the board;
 *   - next_up() calls the most urgent first, earlier arrival on ties.
 */
#include "mintest.h"

#include "clinic.hpp"

#include <string>
#include <vector>

#define CHECK_STR(got_str, want, msg) do {                                  \
        const std::string mt_line = (got_str);                              \
        CHECK_EQ_STR(mt_line.c_str(), (want), (msg));                       \
    } while (0)

static std::string join(const std::vector<std::string> &v) {
    std::string out;
    for (const auto &s : v) {
        out += s;
        out += ",";
    }
    return out;
}

static std::string patient_of(const WaitList &wl, const std::string &ticket) {
    const Visit *v = wl.lookup(ticket);
    return v ? v->patient : "(none)";
}

/* Tuesday morning's board: six walk-ins, owners of T-103/T-104/T-106
 * stepped out with holds that lapse by minute 40. */
static WaitList tuesday() {
    WaitList wl;
    wl.arrive({"T-101", "Peanut (dachshund)", 3, 60});
    wl.arrive({"T-102", "Biscuit (beagle)", 2, 45});
    wl.arrive({"T-103", "Mochi (cat)", 3, 25});
    wl.arrive({"T-104", "Ziggy (corgi)", 1, 30});
    wl.arrive({"T-105", "Clover (lab)", 4, 90});
    wl.arrive({"T-106", "Rumble (mastiff)", 2, 35});
    return wl;
}

TEST(fresh_board_reads_back_correctly) {
    WaitList wl = tuesday();
    CHECK_EQ_INT(wl.size(), 6, "six visits waiting");
    CHECK_STR(join(wl.order()), "T-101,T-102,T-103,T-104,T-105,T-106,",
              "arrival order preserved");
    CHECK_STR(patient_of(wl, "T-104"), "Ziggy (corgi)", "T-104 is Ziggy");
    CHECK_STR(patient_of(wl, "T-106"), "Rumble (mastiff)", "T-106 is Rumble");
    CHECK_STR(patient_of(wl, "T-999"), "(none)", "unknown ticket is nullptr");
    CHECK_STR(join(wl.next_up(3)), "T-104,T-102,T-106,",
              "urgency order with arrival ties");
    CHECK_EQ_INT(wl.dropped_total(), 0, "nothing dropped yet");
}

TEST(sweep_with_no_lapsed_holds_changes_nothing) {
    WaitList wl = tuesday();
    CHECK_EQ_INT(wl.sweep_holds(5), 0, "no hold lapses by minute 5");
    CHECK_EQ_INT(wl.size(), 6, "board unchanged");
    CHECK_STR(patient_of(wl, "T-105"), "Clover (lab)", "lookups unchanged");
    CHECK_EQ_INT(wl.dropped_total(), 0, "total unchanged");
}

TEST(the_forty_minute_sweep_clears_every_lapsed_hold) {
    WaitList wl = tuesday();
    CHECK_EQ_INT(wl.sweep_holds(40), 3,
                 "T-103, T-104 and T-106 lapse by minute 40");
    CHECK_EQ_INT(wl.size(), 3, "three visits remain");
    CHECK_STR(join(wl.order()), "T-101,T-102,T-105,",
              "exactly the live visits remain, in arrival order");
    CHECK_EQ_INT(wl.dropped_total(), 3, "total agrees with the board");
}

TEST(desk_lookups_stay_correct_after_a_sweep) {
    WaitList wl = tuesday();
    wl.sweep_holds(40);
    CHECK_STR(patient_of(wl, "T-101"), "Peanut (dachshund)",
              "T-101 still calls up Peanut");
    CHECK_STR(patient_of(wl, "T-102"), "Biscuit (beagle)",
              "T-102 still calls up Biscuit");
    CHECK_STR(patient_of(wl, "T-105"), "Clover (lab)",
              "T-105 still calls up Clover");
    CHECK_STR(patient_of(wl, "T-103"), "(none)", "T-103 left the board");
    CHECK_STR(patient_of(wl, "T-104"), "(none)", "T-104 left the board");
    CHECK_STR(patient_of(wl, "T-106"), "(none)", "T-106 left the board");
    CHECK_STR(join(wl.next_up(2)), "T-102,T-101,",
              "next-up reflects the swept board");
}

TEST(sweeping_twice_finds_nothing_new) {
    WaitList wl = tuesday();
    CHECK_EQ_INT(wl.sweep_holds(40), 3, "first sweep drops three");
    CHECK_EQ_INT(wl.sweep_holds(40), 0, "second sweep finds nothing");
    CHECK_EQ_INT(wl.size(), 3, "board stable");
    CHECK_EQ_INT(wl.dropped_total(), 3, "total counts real removals only");
}

TEST(staged_sweeps_keep_lookups_and_totals_honest) {
    WaitList wl = tuesday();
    CHECK_EQ_INT(wl.sweep_holds(26), 1, "only T-103 lapses by minute 26");
    CHECK_STR(join(wl.order()), "T-101,T-102,T-104,T-105,T-106,",
              "five visits remain after the early sweep");
    CHECK_STR(patient_of(wl, "T-104"), "Ziggy (corgi)",
              "T-104 still calls up Ziggy after the early sweep");
    CHECK_STR(patient_of(wl, "T-105"), "Clover (lab)",
              "T-105 still calls up Clover after the early sweep");
    CHECK_STR(patient_of(wl, "T-106"), "Rumble (mastiff)",
              "T-106 still calls up Rumble after the early sweep");
    CHECK_EQ_INT(wl.sweep_holds(40), 2, "T-104 and T-106 lapse by minute 40");
    CHECK_EQ_INT(wl.dropped_total(), 3, "totals accumulate across sweeps");
    CHECK_STR(patient_of(wl, "T-105"), "Clover (lab)",
              "T-105 still calls up Clover after both sweeps");
    CHECK_STR(join(wl.order()), "T-101,T-102,T-105,", "final board");
}

TEST(a_run_of_adjacent_lapsed_holds_clears_completely) {
    WaitList wl;
    wl.arrive({"T-201", "Nori (cat)", 3, 10});
    wl.arrive({"T-202", "Taco (chihuahua)", 2, 10});
    wl.arrive({"T-203", "Bagel (pug)", 4, 10});
    wl.arrive({"T-204", "Miso (cat)", 5, 10});
    CHECK_EQ_INT(wl.sweep_holds(20), 4, "all four holds lapsed");
    CHECK_EQ_INT(wl.size(), 0, "the board is empty");
    CHECK_STR(join(wl.order()), "", "no tickets left");
    CHECK_EQ_INT(wl.dropped_total(), 4, "all four counted");
    CHECK_STR(patient_of(wl, "T-202"), "(none)", "cleared tickets are gone");
}

TEST(arrivals_after_a_sweep_slot_in_cleanly) {
    WaitList wl = tuesday();
    wl.sweep_holds(40);
    wl.arrive({"T-107", "Waffles (terrier)", 1, 120});
    CHECK_EQ_INT(wl.size(), 4, "three survivors plus the new arrival");
    CHECK_STR(join(wl.order()), "T-101,T-102,T-105,T-107,",
              "new arrival queues at the back");
    CHECK_STR(patient_of(wl, "T-107"), "Waffles (terrier)",
              "new ticket calls up the new patient");
    CHECK_STR(patient_of(wl, "T-101"), "Peanut (dachshund)",
              "old tickets still call up the right patients");
    CHECK_STR(join(wl.next_up(3)), "T-107,T-102,T-101,",
              "urgent newcomer jumps the next-up list");
}

TEST(sweeping_an_empty_board_is_fine) {
    WaitList wl;
    CHECK_EQ_INT(wl.sweep_holds(100), 0, "nothing to drop");
    CHECK_EQ_INT(wl.size(), 0, "still empty");
    CHECK_EQ_INT(wl.dropped_total(), 0, "still zero");
}

int main(void) {
    RUN(fresh_board_reads_back_correctly);
    RUN(sweep_with_no_lapsed_holds_changes_nothing);
    RUN(the_forty_minute_sweep_clears_every_lapsed_hold);
    RUN(desk_lookups_stay_correct_after_a_sweep);
    RUN(sweeping_twice_finds_nothing_new);
    RUN(staged_sweeps_keep_lookups_and_totals_honest);
    RUN(a_run_of_adjacent_lapsed_holds_clears_completely);
    RUN(arrivals_after_a_sweep_slot_in_cleanly);
    RUN(sweeping_an_empty_board_is_fine);
    return mt_summary();
}
