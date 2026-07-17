/* Acceptance tests for the intake grading module (silo.h / band.h / grade.c).
 * Build and run with `make test`. Bands are half-open [lo, hi); a moisture
 * reading outside every band grades as reject. Blend moisture is the
 * tonnage-weighted mean over the lots.
 */
#include "silo.h"
#include "band.h"
#include "mintest.h"

static double dabs(double x) { return x < 0.0 ? -x : x; }

#define CHECK_CLOSE(got, want, msg) \
    CHECK(dabs((got) - (want)) < 1e-9, msg)

static const struct moisture_band SEASON[] = {
    { 0.0, 13.5, GRADE_PRIME },
    { 13.5, 15.5, GRADE_STANDARD },
    { 15.5, 18.0, GRADE_FEED },
};
static const size_t NBANDS = sizeof SEASON / sizeof SEASON[0];

TEST(band_lookup_hits_the_right_band) {
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 12.0), 0, "12.0 sits in the prime band");
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 14.2), 1, "14.2 sits in the standard band");
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 16.9), 2, "16.9 sits in the feed band");
}

TEST(band_edges_are_low_inclusive_high_exclusive) {
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 13.5), 1, "13.5 belongs to the band it opens");
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 15.5), 2, "15.5 belongs to the band it opens");
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 18.0), -1, "18.0 falls off the top");
    CHECK_EQ_INT(band_for(SEASON, NBANDS, 0.0), 0, "bone dry still grades");
}

TEST(lot_grades_follow_the_bands) {
    struct silo_lot dry   = { "B-101", 24.0, 11.75 };
    struct silo_lot damp  = { "B-102", 18.0, 14.0 };
    struct silo_lot wet   = { "B-103", 9.0, 17.25 };
    struct silo_lot soggy = { "B-104", 4.0, 19.5 };
    CHECK_EQ_INT(lot_grade(SEASON, NBANDS, &dry), GRADE_PRIME, "dry lot grades prime");
    CHECK_EQ_INT(lot_grade(SEASON, NBANDS, &damp), GRADE_STANDARD, "damp lot grades standard");
    CHECK_EQ_INT(lot_grade(SEASON, NBANDS, &wet), GRADE_FEED, "wet lot grades feed");
    CHECK_EQ_INT(lot_grade(SEASON, NBANDS, &soggy), GRADE_REJECT, "soggy lot is rejected");
}

TEST(blend_is_tonnage_weighted) {
    struct silo_lot lots[] = {
        { "B-201", 10.0, 12.0 },
        { "B-202", 30.0, 14.0 },
    };
    CHECK_CLOSE(total_tons(lots, 2), 40.0, "tons add up");
    CHECK_CLOSE(blend_moisture(lots, 2), 13.5, "blend leans toward the big lot");
}

TEST(empty_intake_board_is_calm) {
    CHECK_CLOSE(total_tons(NULL, 0), 0.0, "no lots, no tons");
    CHECK_CLOSE(blend_moisture(NULL, 0), 0.0, "no lots, no blend");
    CHECK_EQ_INT(band_for(SEASON, 0, 14.0), -1, "no bands published yet");
}

int main(void) {
    RUN(band_lookup_hits_the_right_band);
    RUN(band_edges_are_low_inclusive_high_exclusive);
    RUN(lot_grades_follow_the_bands);
    RUN(blend_is_tonnage_weighted);
    RUN(empty_intake_board_is_calm);
    return mt_summary();
}
