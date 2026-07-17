/* Acceptance tests for the inventory sheet formatter (report.h).
 * Build and run with `make test`.
 *
 * Layout contract: SKU 10 | DESC 24 | BIN 8 | QTY 6 (right-aligned),
 * columns joined with " | " -> every line is exactly 57 characters.
 */
#include "mintest.h"
#include "report.h"

#include <stdlib.h>

static const char *HDR =
    "SKU        | DESCRIPTION              | BIN      |    QTY";

TEST(plain_row_lines_up) {
    item it = {"BX-0042", "hex bolt kit", "A-07", 250};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "BX-0042    | hex bolt kit             | A-07     |    250",
            "short values pad their columns");
        CHECK_EQ_INT(strlen(row), 57, "row is exactly 57 characters");
        free(row);
    }
}

TEST(long_description_is_cut_at_the_column) {
    item it = {"BX-0117", "self-tapping screw assortment box", "B-12", 12};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "BX-0117    | self-tapping screw assor | B-12     |     12",
            "description fills all 24 columns when cut");
        CHECK_EQ_INT(strlen(row), 57, "row is exactly 57 characters");
        free(row);
    }
}

TEST(long_sku_is_cut_at_the_column) {
    item it = {"PALLET-JACK-01", "manual pallet jack", "DOCK", 2};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "PALLET-JAC | manual pallet jack       | DOCK     |      2",
            "sku keeps its first 10 characters");
        CHECK_EQ_INT(strlen(row), 57, "row is exactly 57 characters");
        free(row);
    }
}

TEST(exact_width_text_needs_no_padding) {
    item it = {"RMA-BIN", "returned units hold area", "QA-1", 48};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "RMA-BIN    | returned units hold area | QA-1     |     48",
            "a 24-char description fits exactly");
        CHECK_EQ_INT(strlen(row), 57, "row is exactly 57 characters");
        free(row);
    }
}

TEST(empty_text_and_zero_quantity) {
    item it = {"K-1", "", "D-1", 0};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "K-1        |                          | D-1      |      0",
            "empty description is all spaces, qty 0 right-aligns");
        CHECK_EQ_INT(strlen(row), 57, "row is exactly 57 characters");
        free(row);
    }
}

TEST(oversized_quantity_shows_hashes) {
    item it = {"LBL-9", "shelf labels", "C-03", 1234567};
    char *row = report_row(&it);
    CHECK(row != NULL, "row allocates");
    if (row) {
        CHECK_EQ_STR(row,
            "LBL-9      | shelf labels             | C-03     | ######",
            "seven digits into six columns marks the cell");
        free(row);
    }
}

TEST(sheet_is_header_rule_then_rows) {
    item items[] = {
        {"BX-0042", "hex bolt kit", "A-07", 250},
        {"BX-0117", "self-tapping screw assortment box", "B-12", 12},
        {"LBL-9", "shelf labels", "C-03", 1234567},
    };
    char *sheet = report_render(items, 3);
    CHECK(sheet != NULL, "sheet allocates");
    if (sheet == NULL)
        return;
    char rule[64];
    memset(rule, '-', 57);
    rule[57] = '\0';
    char want[512];
    snprintf(want, sizeof want, "%s\n%s\n%s\n%s\n%s\n", HDR, rule,
        "BX-0042    | hex bolt kit             | A-07     |    250",
        "BX-0117    | self-tapping screw assor | B-12     |     12",
        "LBL-9      | shelf labels             | C-03     | ######");
    CHECK_EQ_STR(sheet, want, "whole sheet matches line for line");
    free(sheet);
}

TEST(empty_sheet_still_has_header_and_rule) {
    char *sheet = report_render(NULL, 0);
    CHECK(sheet != NULL, "sheet allocates");
    if (sheet == NULL)
        return;
    char rule[64];
    memset(rule, '-', 57);
    rule[57] = '\0';
    char want[192];
    snprintf(want, sizeof want, "%s\n%s\n", HDR, rule);
    CHECK_EQ_STR(sheet, want, "no items means header and rule only");
    free(sheet);
}

int main(void) {
    RUN(plain_row_lines_up);
    RUN(long_description_is_cut_at_the_column);
    RUN(long_sku_is_cut_at_the_column);
    RUN(exact_width_text_needs_no_padding);
    RUN(empty_text_and_zero_quantity);
    RUN(oversized_quantity_shows_hashes);
    RUN(sheet_is_header_rule_then_rows);
    RUN(empty_sheet_still_has_header_and_rule);
    return mt_summary();
}
