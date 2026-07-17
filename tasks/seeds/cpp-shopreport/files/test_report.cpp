/* Acceptance tests for the workshop end-of-week report (report.hpp/report.cpp).
 * Build and run with `make test`.
 *
 * Billing rule pinned here: labor for ONE order bills as
 * minutes * rate_cents_per_hour / 60 in integer arithmetic, truncated per
 * order BEFORE summing. Revenue counts closed orders only. Tie and
 * stability rules are pinned exactly by the fixtures below.
 */
#include "mintest.h"

#include "report.hpp"

#include <map>
#include <string>
#include <vector>

#define CHECK_STR(got_str, want, msg) do {                                  \
        const std::string mt_line = (got_str);                              \
        CHECK_EQ_STR(mt_line.c_str(), (want), (msg));                       \
    } while (0)

static std::vector<WorkOrder> shop() {
    return {
        {"WO-1001", "irena", "brakes",      45,  2350, true},
        {"WO-1002", "marco", "drivetrain",  90,  7800, true},
        {"WO-1003", "irena", "wheels",      30,     0, false},
        {"WO-1004", "sam",   "brakes",      50,   199, true},
        {"WO-1005", "marco", "fitting",     60,     0, false},
        {"WO-1006", "irena", "drivetrain",  25,  1520, true},
        {"WO-1007", "sam",   "wheels",      30,  4400, false},
        {"WO-1008", "marco", "brakes",      10,   650, true},
        {"WO-1009", "noor",  "e-bike",     120, 15900, true},
        {"WO-1010", "noor",  "fitting",     60,     0, false},
        {"WO-1011", "tovah", "wheels",      40,   800, false},
    };
}

static std::string join(const std::vector<std::string> &v) {
    std::string out;
    for (const auto &s : v) {
        out += s;
        out += ",";
    }
    return out;
}

static std::string join(const std::map<std::string, long> &m) {
    std::string out;
    for (const auto &[k, v] : m) {
        out += k;
        out += "=";
        out += std::to_string(v);
        out += ";";
    }
    return out;
}

TEST(total_revenue_counts_closed_orders_only) {
    CHECK_EQ_INT(shop_revenue_cents(shop(), 9000), 79419,
                 "labor at 9000c/h plus parts, closed orders only");
    CHECK_EQ_INT(shop_revenue_cents(shop(), 0), 28419,
                 "at a zero rate only parts remain");
}

TEST(labor_truncates_per_order_not_on_the_grand_total) {
    /* At 5000c/h three closed orders bill fractional labor; each must be
     * truncated on its own. Summing minutes first gives 56752 — wrong. */
    CHECK_EQ_INT(shop_revenue_cents(shop(), 5000), 56751,
                 "per-order integer division, then the sum");
}

TEST(revenue_by_mechanic_covers_exactly_the_closing_mechanics) {
    CHECK_STR(join(revenue_by_mechanic(shop(), 9000)),
              "irena=14370;marco=23450;noor=33900;sam=7699;",
              "per-mechanic totals over closed orders");
    /* tovah only has an open order and must not appear at all */
    CHECK_EQ_INT(revenue_by_mechanic(shop(), 9000).count("tovah"), 0,
                 "mechanics without closed orders are absent, not zero");
}

TEST(backlog_sorts_longest_labor_first_with_id_ties) {
    CHECK_STR(join(backlog_ids(shop())),
              "WO-1005,WO-1010,WO-1011,WO-1003,WO-1007,",
              "open orders by minutes descending, ties by id ascending");
}

TEST(parts_split_keeps_arrival_order_on_both_sides) {
    PartsSplit s = split_by_parts(shop(), 1000);
    CHECK_STR(join(s.heavy), "WO-1001,WO-1002,WO-1006,WO-1009,",
              "closed orders at or above the threshold, arrival order");
    CHECK_STR(join(s.light), "WO-1004,WO-1008,",
              "closed orders below the threshold, arrival order");
}

TEST(parts_split_threshold_is_inclusive) {
    PartsSplit s = split_by_parts(shop(), 650);
    CHECK_STR(join(s.heavy), "WO-1001,WO-1002,WO-1006,WO-1008,WO-1009,",
              "an order exactly at the threshold counts as heavy");
    CHECK_STR(join(s.light), "WO-1004,", "only WO-1004 stays below 650");
}

TEST(busiest_category_counts_open_and_closed_minutes) {
    /* fitting and e-bike are tied at 120 booked minutes; the tie goes to
     * the lexicographically smaller name */
    CHECK_STR(busiest_category(shop()), "e-bike",
              "total minutes across all orders, smallest name wins ties");
}

TEST(busiest_category_without_the_tie) {
    std::vector<WorkOrder> orders = shop();
    orders.push_back({"WO-1012", "sam", "fitting", 15, 0, false});
    CHECK_STR(busiest_category(orders), "fitting",
              "adding open minutes moves the crown");
}

TEST(empty_shop_produces_an_empty_report) {
    std::vector<WorkOrder> none;
    CHECK_EQ_INT(shop_revenue_cents(none, 9000), 0, "no orders, no revenue");
    CHECK_EQ_INT(revenue_by_mechanic(none, 9000).size(), 0,
                 "no orders, no mechanics");
    CHECK_EQ_INT(backlog_ids(none).size(), 0, "no orders, no backlog");
    PartsSplit s = split_by_parts(none, 100);
    CHECK_EQ_INT(s.heavy.size(), 0, "no orders, no heavy side");
    CHECK_EQ_INT(s.light.size(), 0, "no orders, no light side");
    CHECK_STR(busiest_category(none), "", "no orders, no busiest category");
}

TEST(all_open_shop_bills_nothing_but_still_reports) {
    std::vector<WorkOrder> orders = {
        {"WO-2001", "kim", "brakes", 20, 500, false},
        {"WO-2002", "kim", "wheels", 20, 900, false},
    };
    CHECK_EQ_INT(shop_revenue_cents(orders, 9000), 0,
                 "open orders never bill");
    CHECK_EQ_INT(revenue_by_mechanic(orders, 9000).size(), 0,
                 "nobody billed anything");
    CHECK_STR(join(backlog_ids(orders)), "WO-2001,WO-2002,",
              "equal minutes fall back to id order");
    CHECK_STR(busiest_category(orders), "brakes",
              "20-20 category tie goes alphabetically");
}

TEST(single_closed_order_report) {
    std::vector<WorkOrder> orders = {
        {"WO-3001", "ada", "e-bike", 61, 120, true},
    };
    CHECK_EQ_INT(shop_revenue_cents(orders, 5900), 6118,
                 "61 minutes at 5900c/h truncates to 5998, plus 120 parts");
    CHECK_STR(join(revenue_by_mechanic(orders, 5900)), "ada=6118;",
              "one mechanic, one total");
    CHECK_EQ_INT(backlog_ids(orders).size(), 0, "closed orders are not backlog");
}

int main(void) {
    RUN(total_revenue_counts_closed_orders_only);
    RUN(labor_truncates_per_order_not_on_the_grand_total);
    RUN(revenue_by_mechanic_covers_exactly_the_closing_mechanics);
    RUN(backlog_sorts_longest_labor_first_with_id_ties);
    RUN(parts_split_keeps_arrival_order_on_both_sides);
    RUN(parts_split_threshold_is_inclusive);
    RUN(busiest_category_counts_open_and_closed_minutes);
    RUN(busiest_category_without_the_tie);
    RUN(empty_shop_produces_an_empty_report);
    RUN(all_open_shop_bills_nothing_but_still_reports);
    RUN(single_closed_order_report);
    return mt_summary();
}
