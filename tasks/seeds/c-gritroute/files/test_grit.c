/* Acceptance tests for the grit-route planner. Build and run with
 * `make test`. Rates, route totals, the season reserve and the depot
 * summary line are pinned exactly. */
#include "mintest.h"
#include "spread.h"
#include "route.h"
#include "summary.h"

static const struct leg morning[] = {
    {12, 1}, /* ring road, wet frost */
    {5, 3},  /* viaduct, packed ice */
    {8, 0},  /* depot loop, frost watch */
};

TEST(rates_follow_the_severity_bands) {
    CHECK_EQ_INT(grit_rate_for(0), 8, "frost watch rate");
    CHECK_EQ_INT(grit_rate_for(1), 12, "wet frost rate");
    CHECK_EQ_INT(grit_rate_for(2), 20, "snowfall rate");
    CHECK_EQ_INT(grit_rate_for(3), 28, "packed ice rate");
    CHECK_EQ_INT(grit_rate_for(-2), 8, "below range clamps down");
    CHECK_EQ_INT(grit_rate_for(9), 28, "above range clamps up");
}

TEST(leg_spreads_cost_km_times_rate) {
    CHECK_EQ_INT(spread_total_kg(10, 2), 200, "ten km of snowfall");
    CHECK_EQ_INT(spread_total_kg(1, 0), 8, "one km at the light rate");
    CHECK_EQ_INT(spread_total_kg(0, 3), 0, "no distance, no grit");
    CHECK_EQ_INT(spread_total_kg(-4, 1), 0, "negative distance costs nothing");
}

TEST(route_totals_sum_the_legs) {
    CHECK_EQ_INT(route_leg_total(morning, 3), 25, "morning route km");
    CHECK_EQ_INT(route_plan_kg(morning, 3), 348, "morning route kg");
    CHECK_EQ_INT(route_leg_total(morning, 0), 0, "empty route has no km");
    CHECK_EQ_INT(route_plan_kg(morning, 0), 0, "empty route spreads nothing");
}

TEST(reserve_starts_at_the_season_allocation) {
    CHECK_EQ_INT(grit_reserve_kg, 1000, "full allocation on the truck");
    CHECK_EQ_INT(route_reserve_after(morning, 0), 1000,
                 "empty route leaves the allocation alone");
    CHECK_EQ_INT(route_reserve_after(morning, 3), 652,
                 "morning route draws the reserve down");
}

TEST(summary_line_matches_the_depot_board) {
    char buf[96];

    grit_summary_line(buf, sizeof buf, morning, 3);
    CHECK_EQ_STR(buf, "legs=3 km=25 plan=348kg reserve=652kg worst-rate=28",
                 "full morning summary");
    grit_summary_line(buf, sizeof buf, morning, 0);
    CHECK_EQ_STR(buf, "legs=0 km=0 plan=0kg reserve=1000kg worst-rate=8",
                 "empty route summary");
}

int main(void) {
    RUN(rates_follow_the_severity_bands);
    RUN(leg_spreads_cost_km_times_rate);
    RUN(route_totals_sum_the_legs);
    RUN(reserve_starts_at_the_season_allocation);
    RUN(summary_line_matches_the_depot_board);
    return mt_summary();
}
