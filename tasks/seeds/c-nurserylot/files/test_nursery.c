/* Acceptance tests for the nursery lot board. Build and run with
 * `make test`. Row maths, stocking summaries and board labels are
 * pinned exactly. */
#include "mintest.h"
#include "plant.h"
#include "units.h"
#include "lot.h"

static const struct bed_row birch_bed[] = {
    {40, 32},
    {40, 40},
    {24, 5},
};

TEST(free_slots_never_go_negative) {
    struct bed_row over = {30, 34}; /* heeled-in extras happen */

    CHECK_EQ_INT(bed_free_slots(&birch_bed[0]), 8, "row with space");
    CHECK_EQ_INT(bed_free_slots(&birch_bed[1]), 0, "full row");
    CHECK_EQ_INT(bed_free_slots(&over), 0, "overplanted row clamps to zero");
}

TEST(bed_totals_sum_the_rows) {
    CHECK_EQ_INT(bed_free_total(birch_bed, 3), 27, "free across the bed");
    CHECK_EQ_INT(bed_free_total(birch_bed, 0), 0, "no rows, no space");
}

TEST(stock_level_summarizes_a_bed) {
    struct stock_lev lev = plant_stock_level(birch_bed, 3);

    CHECK_EQ_INT(lev.planted, 77, "pots in the ground");
    CHECK_EQ_INT(lev.free_slots, 27, "positions still open");
    CHECK_EQ_INT(lev.pct_full, 74, "77 of 104 slots, rounded down");

    lev = plant_stock_level(birch_bed, 0);
    CHECK_EQ_INT(lev.planted, 0, "empty bed has nothing planted");
    CHECK_EQ_INT(lev.pct_full, 0, "empty bed is 0% full, not a div crash");
}

TEST(rows_needed_rounds_up) {
    CHECK_EQ_INT(lot_rows_needed(140, 40), 4, "3.5 rows means 4 rows");
    CHECK_EQ_INT(lot_rows_needed(120, 40), 3, "exact fit stays exact");
    CHECK_EQ_INT(lot_rows_needed(1, 40), 1, "one pot still takes a row");
    CHECK_EQ_INT(lot_rows_needed(0, 40), 0, "nothing to plant");
    CHECK_EQ_INT(lot_rows_needed(50, 0), 0, "bogus row size needs no rows");
}

TEST(sheet_totals_and_labels) {
    struct plant_rec sheet[] = {
        {"birch", 140},
        {"rowan", 60},
        {"field maple", 25},
    };
    char buf[48];

    CHECK_EQ_INT(lot_species_total(sheet, 3), 225, "sheet total");
    CHECK_EQ_INT(lot_species_total(sheet, 0), 0, "empty sheet totals zero");

    lot_label(buf, sizeof buf, 7, &sheet[0]);
    CHECK_EQ_STR(buf, "LOT-07 birch x140", "label for a stocked lot");
    lot_label(buf, sizeof buf, 12, NULL);
    CHECK_EQ_STR(buf, "LOT-12 empty", "label for an empty lot");
}

int main(void) {
    RUN(free_slots_never_go_negative);
    RUN(bed_totals_sum_the_rows);
    RUN(stock_level_summarizes_a_bed);
    RUN(rows_needed_rounds_up);
    RUN(sheet_totals_and_labels);
    return mt_summary();
}
