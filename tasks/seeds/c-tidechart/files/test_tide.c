/* Acceptance tests for the harbor tide chart helpers. Build and run with
 * `make test`. Table parsing, interpolation, water-state tags and chart
 * rows are all pinned here — the maths and layout must not change. */
#include "mintest.h"
#include "tide.h"
#include "chart.h"

static double dabs(double x) { return x < 0.0 ? -x : x; }

#define CHECK_CLOSE(got, want, msg) \
    CHECK(dabs((got) - (want)) < 1e-9, msg)

TEST(parses_a_plain_table_line) {
    struct tide_entry e;
    CHECK_EQ_INT(tide_parse_entry("06:24 1.8", &e), 0, "plain line parses");
    CHECK_EQ_INT(e.minute, 384, "06:24 is 384 minutes after midnight");
    CHECK_CLOSE(e.height_m, 1.8, "height comes through");
}

TEST(parses_with_leading_and_extra_blanks) {
    struct tide_entry e;
    CHECK_EQ_INT(tide_parse_entry("  18:06   0.45", &e), 0,
                 "blanks around the fields are fine");
    CHECK_EQ_INT(e.minute, 1086, "18:06 is 1086 minutes");
    CHECK_CLOSE(e.height_m, 0.45, "height after extra blanks");
}

TEST(rejects_malformed_table_lines) {
    struct tide_entry e;
    CHECK_EQ_INT(tide_parse_entry("24:00 1.0", &e), -1, "hour out of range");
    CHECK_EQ_INT(tide_parse_entry("12:61 1.0", &e), -1, "minute out of range");
    CHECK_EQ_INT(tide_parse_entry("12:6 1.0", &e), -1,
                 "minutes must be two digits");
    CHECK_EQ_INT(tide_parse_entry("12-30 1.0", &e), -1, "wrong separator");
    CHECK_EQ_INT(tide_parse_entry("06:24", &e), -1, "height missing");
    CHECK_EQ_INT(tide_parse_entry("high 1.0", &e), -1, "no digits at all");
}

TEST(interpolates_between_the_marks) {
    CHECK_CLOSE(tide_interp(0.4, 2.0, 0, 360), 0.4, "start of span is lo");
    CHECK_CLOSE(tide_interp(0.4, 2.0, 180, 360), 1.2, "halfway is midpoint");
    CHECK_CLOSE(tide_interp(0.4, 2.0, 360, 360), 2.0, "end of span is hi");
    CHECK_CLOSE(tide_interp(0.4, 2.0, 90, 360), 0.8, "quarter of the way");
}

TEST(interpolation_clamps_outside_the_span) {
    CHECK_CLOSE(tide_interp(0.4, 2.0, -30, 360), 0.4, "before the span");
    CHECK_CLOSE(tide_interp(0.4, 2.0, 400, 360), 2.0, "after the span");
    CHECK_CLOSE(tide_interp(1.1, 1.7, 50, 0), 1.1, "empty span pins to lo");
    CHECK_CLOSE(tide_interp(1.1, 1.7, 50, -5), 1.1, "bogus span pins to lo");
}

TEST(water_state_tags_use_quarter_bands) {
    /* lo 0.4, hi 2.0 -> band 0.4: <=0.8 slack-low, >=1.6 slack-high */
    CHECK_EQ_STR(tide_slot_label(0.4, 0.4, 2.0), "slack-low", "at lo");
    CHECK_EQ_STR(tide_slot_label(0.8, 0.4, 2.0), "slack-low",
                 "band edge is still slack-low");
    CHECK_EQ_STR(tide_slot_label(0.81, 0.4, 2.0), "moving", "just above band");
    CHECK_EQ_STR(tide_slot_label(1.59, 0.4, 2.0), "moving", "just below band");
    CHECK_EQ_STR(tide_slot_label(1.6, 0.4, 2.0), "slack-high",
                 "band edge is slack-high");
    CHECK_EQ_STR(tide_slot_label(2.0, 0.4, 2.0), "slack-high", "at hi");
}

TEST(chart_rows_have_the_pinned_layout) {
    char buf[64];
    tide_render_row(buf, sizeof buf, "Dockside", 1.2, 0.4, 2.0);
    CHECK_EQ_STR(buf, "Dockside   |  1.20m | moving", "short name is padded");
    tide_render_row(buf, sizeof buf, "Fuel Pontoon", 0.55, 0.4, 2.0);
    CHECK_EQ_STR(buf, "Fuel Ponto |  0.55m | slack-low",
                 "long name is cut at ten columns");
    tide_render_row(buf, sizeof buf, "Slip 9", 1.95, 0.4, 2.0);
    CHECK_EQ_STR(buf, "Slip 9     |  1.95m | slack-high",
                 "state tag rides on the row");
}

int main(void) {
    RUN(parses_a_plain_table_line);
    RUN(parses_with_leading_and_extra_blanks);
    RUN(rejects_malformed_table_lines);
    RUN(interpolates_between_the_marks);
    RUN(interpolation_clamps_outside_the_span);
    RUN(water_state_tags_use_quarter_bands);
    RUN(chart_rows_have_the_pinned_layout);
    return mt_summary();
}
