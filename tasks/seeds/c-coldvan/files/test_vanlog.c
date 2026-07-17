#include <stdio.h>
#include <stdlib.h>

#include "mintest.h"
#include "vanlog.h"

/* Fixture writer: count header (possibly lying, for damage tests), then
 * n raw records. */
static void write_trip(const char *path, size_t header,
                       const struct VanRec *recs, size_t n) {
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(&header, sizeof header, 1, f) != 1) {
        fprintf(stderr, "fixture write failed: %s\n", path);
        exit(2);
    }
    if (n > 0 && fwrite(recs, sizeof *recs, n, f) != n) {
        fprintf(stderr, "fixture write failed: %s\n", path);
        exit(2);
    }
    fclose(f);
}

static const struct VanRec DELIVERY[5] = {
    { 0,  4.0, 'T' },
    { 10, 4.6, 'T' },
    { 15, 6.1, 'A' },
    { 18, 5.0, 'D' },
    { 30, 3.2, 'T' },
};

TEST(load_reads_a_whole_trip_back) {
    write_trip("trip_full.bin", 5, DELIVERY, 5);
    struct VanRec got[8];
    CHECK_EQ_INT(vanlog_load("trip_full.bin", got, 8), 5, "five entries");
    CHECK_EQ_INT(got[0].minute, 0, "first entry minute");
    CHECK(got[2].temp_c == 6.1, "alert temp survives the round trip");
    CHECK_EQ_INT(got[4].kind, 'T', "last entry kind");
}

TEST(summary_counts_each_kind) {
    struct VanSummary s = vanlog_summarize(DELIVERY, 5);
    CHECK_EQ_INT(s.readings, 3, "three plain readings");
    CHECK_EQ_INT(s.alerts, 1, "one alert");
    CHECK_EQ_INT(s.events, 2, "alerts count as events alongside the door");
}

TEST(summary_min_max_only_use_temp_bearing_entries) {
    struct VanSummary s = vanlog_summarize(DELIVERY, 5);
    CHECK(s.min_c == 3.2, "coldest reading");
    CHECK(s.max_c == 6.1, "warmest comes from the alert entry");
    CHECK(s.worst_alert_c == 6.1, "worst alert temp");
}

TEST(door_only_trip_reports_zero_range) {
    struct VanRec doors[2] = { { 5, 4.9, 'D' }, { 9, 4.8, 'D' } };
    struct VanSummary s = vanlog_summarize(doors, 2);
    CHECK_EQ_INT(s.events, 2, "two door events");
    CHECK_EQ_INT(s.readings, 0, "no readings");
    CHECK_EQ_INT(s.alerts, 0, "no alerts");
    CHECK(s.min_c == 0.0, "no temp entries means zero min");
    CHECK(s.max_c == 0.0, "no temp entries means zero max");
    CHECK(s.worst_alert_c == 0.0, "no alerts means zero worst");
}

TEST(short_file_is_rejected) {
    write_trip("trip_short.bin", 6, DELIVERY, 2);
    struct VanRec got[8];
    CHECK_EQ_INT(vanlog_load("trip_short.bin", got, 8), -1,
                 "header promises more entries than the file holds");
}

TEST(empty_file_is_rejected) {
    FILE *f = fopen("trip_empty.bin", "wb");
    if (!f) { fprintf(stderr, "fixture write failed\n"); exit(2); }
    fclose(f);
    struct VanRec got[4];
    CHECK_EQ_INT(vanlog_load("trip_empty.bin", got, 4), -1,
                 "a file too short for even the count header");
}

TEST(zero_entry_trip_is_fine) {
    write_trip("trip_none.bin", 0, NULL, 0);
    struct VanRec got[4];
    CHECK_EQ_INT(vanlog_load("trip_none.bin", got, 4), 0, "empty trip loads");
}

TEST(oversized_count_is_rejected) {
    write_trip("trip_big.bin", 5, DELIVERY, 5);
    struct VanRec got[3];
    CHECK_EQ_INT(vanlog_load("trip_big.bin", got, 3), -1,
                 "more entries than the caller's buffer");
}

TEST(missing_file_is_rejected) {
    struct VanRec got[4];
    CHECK_EQ_INT(vanlog_load("trip_absent.bin", got, 4), -1, "no such file");
}

int main(void) {
    RUN(load_reads_a_whole_trip_back);
    RUN(summary_counts_each_kind);
    RUN(summary_min_max_only_use_temp_bearing_entries);
    RUN(door_only_trip_reports_zero_range);
    RUN(short_file_is_rejected);
    RUN(empty_file_is_rejected);
    RUN(zero_entry_trip_is_fine);
    RUN(oversized_count_is_rejected);
    RUN(missing_file_is_rejected);
    return mt_summary();
}
