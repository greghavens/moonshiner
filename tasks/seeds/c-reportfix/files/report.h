/* report.h — fixed-width text report for the nightly inventory sheet. */
#ifndef REPORT_H
#define REPORT_H

#include <stddef.h>

typedef struct {
    const char *sku;
    const char *desc;
    const char *bin;
    long qty;
} item;

/* One formatted data row (no newline). Heap-allocated; caller frees.
 * Columns: SKU 10 | DESC 24 | BIN 8 | QTY 6 (right-aligned), joined
 * with " | ". Text longer than its column is cut to the column width;
 * a quantity wider than its column renders as ###### like the
 * spreadsheets do. NULL on allocation failure. */
char *report_row(const item *it);

/* The whole sheet: header line, rule line, then one line per item,
 * each ending in '\n'. Heap-allocated; caller frees. NULL on
 * allocation failure. */
char *report_render(const item *items, size_t n);

#endif /* REPORT_H */
