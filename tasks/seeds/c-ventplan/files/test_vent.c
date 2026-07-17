/* Acceptance tests for the ridge-vent controller (vent.h / vent.c).
 * Build and run with `make test`. The response band is proportional:
 * at or below the setpoint the vent closes, a full band above it the
 * vent is wide open, and actuator ticks are rate-limited by max_step.
 */
#include "vent.h"
#include "mintest.h"

TEST(target_tracks_the_response_band) {
    CHECK_EQ_INT(vent_target_percent(21.0, 24.0, 6.0), 0, "below setpoint stays closed");
    CHECK_EQ_INT(vent_target_percent(24.0, 24.0, 6.0), 0, "at setpoint stays closed");
    CHECK_EQ_INT(vent_target_percent(27.0, 24.0, 6.0), 50, "mid-band is half open");
    CHECK_EQ_INT(vent_target_percent(30.0, 24.0, 6.0), 100, "full band above is wide open");
    CHECK_EQ_INT(vent_target_percent(35.0, 24.0, 6.0), 100, "past the band clamps at open");
    CHECK_EQ_INT(vent_target_percent(25.5, 24.0, 6.0), 25, "quarter band rounds to 25");
}

TEST(degenerate_band_is_all_or_nothing) {
    CHECK_EQ_INT(vent_target_percent(24.1, 24.0, 0.0), 100, "any excess with no band opens");
    CHECK_EQ_INT(vent_target_percent(23.9, 24.0, 0.0), 0, "no excess with no band closes");
}

TEST(steps_are_rate_limited_both_ways) {
    CHECK_EQ_INT(vent_step_toward(20, 80, 25), 45, "opening moves at most max_step");
    CHECK_EQ_INT(vent_step_toward(45, 80, 25), 70, "still short of target");
    CHECK_EQ_INT(vent_step_toward(70, 80, 25), 80, "final tick lands exactly on target");
    CHECK_EQ_INT(vent_step_toward(60, 10, 30), 30, "closing moves at most max_step");
    CHECK_EQ_INT(vent_step_toward(30, 10, 30), 10, "closing lands exactly on target");
    CHECK_EQ_INT(vent_step_toward(50, 50, 25), 50, "at target holds position");
    CHECK_EQ_INT(vent_step_toward(10, 12, 0), 11, "silly max_step still creeps");
}

TEST(plan_walks_to_the_target) {
    int steps[8];
    int n = vent_plan(30.0, 24.0, 6.0, 20, 25, steps, 8);
    CHECK_EQ_INT(n, 4, "hot afternoon takes four ticks");
    CHECK_EQ_INT(steps[0], 45, "first tick");
    CHECK_EQ_INT(steps[1], 70, "second tick");
    CHECK_EQ_INT(steps[2], 95, "third tick");
    CHECK_EQ_INT(steps[3], 100, "fourth tick reaches wide open");
}

TEST(plan_respects_the_cap) {
    int steps[2];
    int n = vent_plan(30.0, 24.0, 6.0, 0, 10, steps, 2);
    CHECK_EQ_INT(n, 2, "cap cuts the plan short");
    CHECK_EQ_INT(steps[0], 10, "first capped tick");
    CHECK_EQ_INT(steps[1], 20, "second capped tick");
}

TEST(plan_already_on_target_is_empty) {
    int steps[4];
    int n = vent_plan(24.0, 24.0, 6.0, 0, 25, steps, 4);
    CHECK_EQ_INT(n, 0, "closed vent on a cool morning stays put");
}

int main(void) {
    RUN(target_tracks_the_response_band);
    RUN(degenerate_band_is_all_or_nothing);
    RUN(steps_are_rate_limited_both_ways);
    RUN(plan_walks_to_the_target);
    RUN(plan_respects_the_cap);
    RUN(plan_already_on_target_is_empty);
    return mt_summary();
}
