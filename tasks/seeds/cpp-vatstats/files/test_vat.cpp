/* Acceptance tests for the vat telemetry summaries (vatstats.h).
 * Build and run with `make test`. peak() and mean() are generic over the
 * container the loggers hand us; BatchLog groups a shift's readings per vat.
 */
#include "mintest.h"

#include "vatstats.h"

#include <deque>
#include <vector>

static double dabs(double x) { return x < 0.0 ? -x : x; }

#define CHECK_CLOSE(got, want, msg) \
    CHECK(dabs((got) - (want)) < 1e-9, msg)

TEST(peak_finds_the_warmest_reading) {
    std::vector<double> ramp{31.5, 32.25, 34.0, 33.5};
    CHECK_CLOSE(vatstats::peak(ramp), 34.0, "peak of the heating ramp");

    std::vector<double> lone{36.75};
    CHECK_CLOSE(vatstats::peak(lone), 36.75, "single reading is its own peak");

    std::vector<double> cooling{34.0, 33.0, 32.0};
    CHECK_CLOSE(vatstats::peak(cooling), 34.0, "peak may be the first sample");
}

TEST(peak_and_mean_of_an_empty_batch_are_zero) {
    std::vector<double> none;
    CHECK_CLOSE(vatstats::peak(none), 0.0, "no readings, no peak");
    CHECK_CLOSE(vatstats::mean(none), 0.0, "no readings, no mean");
}

TEST(summaries_are_container_generic) {
    std::deque<double> feed{30.0, 31.0, 35.0, 32.0};
    CHECK_CLOSE(vatstats::peak(feed), 35.0, "peak over a deque");
    CHECK_CLOSE(vatstats::mean(feed), 32.0, "mean over a deque");
}

TEST(mean_is_plain_arithmetic) {
    std::vector<double> steady{32.0, 32.5, 33.0, 33.5};
    CHECK_CLOSE(vatstats::mean(steady), 32.75, "mean of the hold phase");
}

TEST(batch_log_groups_by_vat) {
    vatstats::BatchLog shift;
    shift.add("vat-2", 31.0);
    shift.add("vat-2", 34.5);
    shift.add("vat-5", 36.0);
    shift.add("vat-2", 33.0);
    shift.add("vat-5", 35.0);

    CHECK_EQ_INT((long long)shift.vats(), 2, "two vats reported this shift");
    CHECK_CLOSE(shift.vat_peak("vat-2"), 34.5, "vat-2 peak");
    CHECK_CLOSE(shift.vat_mean("vat-2"), 32.8333333333333333, "vat-2 mean");
    CHECK_CLOSE(shift.vat_peak("vat-5"), 36.0, "vat-5 peak");
    CHECK_CLOSE(shift.vat_mean("vat-5"), 35.5, "vat-5 mean");
    CHECK_CLOSE(shift.vat_peak("vat-9"), 0.0, "unknown vat reads as zero");
    CHECK_CLOSE(shift.vat_mean("vat-9"), 0.0, "unknown vat reads as zero");
}

int main(void) {
    RUN(peak_finds_the_warmest_reading);
    RUN(peak_and_mean_of_an_empty_batch_are_zero);
    RUN(summaries_are_container_generic);
    RUN(mean_is_plain_arithmetic);
    RUN(batch_log_groups_by_vat);
    return mt_summary();
}
