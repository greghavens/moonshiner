#include "mintest.h"
#include "chime.h"

TEST(hour_strokes_use_the_twelve_hour_bell) {
    CHECK_EQ_INT(chime_strokes(0), 12, "midnight rings twelve");
    CHECK_EQ_INT(chime_strokes(12), 12, "noon rings twelve");
    CHECK_EQ_INT(chime_strokes(1), 1, "one o'clock");
    CHECK_EQ_INT(chime_strokes(13), 1, "13:00 rings once");
    CHECK_EQ_INT(chime_strokes(23), 11, "23:00 rings eleven");
    CHECK_EQ_INT(chime_strokes(6), 6, "six o'clock");
}

TEST(quarter_index_within_the_hour) {
    CHECK_EQ_INT(chime_quarter(0), 0, "top of the hour");
    CHECK_EQ_INT(chime_quarter(14), 0, "still the first quarter");
    CHECK_EQ_INT(chime_quarter(15), 1, "quarter past");
    CHECK_EQ_INT(chime_quarter(44), 2, "half past band ends at :44");
    CHECK_EQ_INT(chime_quarter(45), 3, "quarter to");
    CHECK_EQ_INT(chime_quarter(59), 3, "last minute of the hour");
}

TEST(quarter_minutes_wrap_across_midnight) {
    CHECK_EQ_INT(chime_quarter(1445), 0, "00:05 next day");
    CHECK_EQ_INT(chime_quarter(-10), 3, "23:50 the day before");
    CHECK_EQ_INT(chime_quarter(1440), 0, "exactly midnight again");
}

TEST(bell_letters_cycle_a_through_g) {
    CHECK_EQ_INT(chime_bell_for_slot(0), 'A', "slot zero");
    CHECK_EQ_INT(chime_bell_for_slot(6), 'G', "seventh bell");
    CHECK_EQ_INT(chime_bell_for_slot(7), 'A', "cycle restarts");
    CHECK_EQ_INT(chime_bell_for_slot(700), 'A', "long slots cycle too");
}

TEST(bell_for_hour_and_quarter) {
    CHECK_EQ_INT(chime_bell_for(0, 0), 'A', "first slot of the day");
    CHECK_EQ_INT(chime_bell_for(1, 2), 'G', "slot six");
    CHECK_EQ_INT(chime_bell_for(2, 0), 'B', "slot eight wraps to B");
    CHECK_EQ_INT(chime_bell_for(23, 3), 'E', "last slot of the day");
}

TEST(daily_stroke_budget) {
    /* 2 * (1 + 2 + ... + 12) great-bell strokes plus 3 peals x 24 hours. */
    CHECK_EQ_INT(chime_daily_strokes(), 228, "full day of ringing");
}

int main(void) {
    RUN(hour_strokes_use_the_twelve_hour_bell);
    RUN(quarter_index_within_the_hour);
    RUN(quarter_minutes_wrap_across_midnight);
    RUN(bell_letters_cycle_a_through_g);
    RUN(bell_for_hour_and_quarter);
    RUN(daily_stroke_budget);
    return mt_summary();
}
