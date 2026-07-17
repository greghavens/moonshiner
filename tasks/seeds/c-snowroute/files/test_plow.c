#include <string.h>

#include "mintest.h"
#include "plow.h"
#include "report.h"

static const struct PlowLeg SHIFT[3] = {
    { "Elm St",    4,  120,  3.50 },
    { "Harbor Rd", 11, 2450, 12.25 },
    { "Mill Lane", 2,  80,   1.10 },
};

TEST(salt_total_sums_every_leg) {
    CHECK_EQ_INT(route_salt_total(SHIFT, 3), 2650, "shift salt total");
    CHECK_EQ_INT(route_salt_total(SHIFT, 1), 120, "single leg total");
    CHECK_EQ_INT(route_salt_total(SHIFT, 0), 0, "empty route spreads nothing");
}

TEST(pass_and_km_totals) {
    CHECK_EQ_INT(route_pass_total(SHIFT, 3), 17, "pass total");
    double km = route_km_total(SHIFT, 3);
    CHECK(km > 16.849 && km < 16.851, "km total is 16.85");
}

TEST(refill_flag_trips_strictly_above_hopper) {
    CHECK_EQ_INT(route_needs_refill(SHIFT, 3, 2650), 0,
                 "exactly one hopper load is not a refill");
    CHECK_EQ_INT(route_needs_refill(SHIFT, 3, 2649), 1,
                 "one kg over the hopper needs a refill");
    CHECK_EQ_INT(route_needs_refill(SHIFT, 0, 0), 0,
                 "empty route never refills");
}

TEST(filter_any_accepts_every_leg_via_typedef) {
    leg_filter f = filter_any;
    CHECK_EQ_INT(f(&SHIFT[0], 0x1u), 1, "day shift leg accepted");
    CHECK_EQ_INT(f(&SHIFT[2], 0x0u), 1, "mask never rejects a leg");
    CHECK_EQ_INT(f(NULL, 0x7u), 0, "null leg rejected");
}

TEST(leg_rows_are_fixed_width) {
    char row[80];
    int w = leg_line(row, sizeof row, &SHIFT[0]);
    CHECK_EQ_INT(w, 44, "row width is fixed");
    CHECK_EQ_STR(row, "Elm St           4 passes   120 kg   3.50 km",
                 "elm st row");
    leg_line(row, sizeof row, &SHIFT[1]);
    CHECK_EQ_STR(row, "Harbor Rd       11 passes  2450 kg  12.25 km",
                 "harbor rd row");
}

TEST(report_ends_with_totals_row) {
    char out[512];
    int rows = report_render(out, sizeof out, SHIFT, 3);
    CHECK_EQ_INT(rows, 4, "three legs plus totals");
    CHECK_EQ_STR(out,
        "Elm St           4 passes   120 kg   3.50 km\n"
        "Harbor Rd       11 passes  2450 kg  12.25 km\n"
        "Mill Lane        2 passes    80 kg   1.10 km\n"
        "TOTALS          17 passes  2650 kg  16.85 km\n",
        "full shift report");
}

int main(void) {
    RUN(salt_total_sums_every_leg);
    RUN(pass_and_km_totals);
    RUN(refill_flag_trips_strictly_above_hopper);
    RUN(filter_any_accepts_every_leg_via_typedef);
    RUN(leg_rows_are_fixed_width);
    RUN(report_ends_with_totals_row);
    return mt_summary();
}
