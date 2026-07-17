#ifndef REPORT_H
#define REPORT_H

#include <stddef.h>
#include "plow.h"

/* Render one leg as a fixed-width report row. Returns the row length
 * (as snprintf), so callers can size buffers. */
int leg_line(char *out, size_t cap, const struct PlowLeg *leg);

/* Render the whole shift: one row per leg, then a totals row.
 * Rows are newline-terminated. Returns the number of rows written. */
int report_render(char *out, size_t cap,
                  const struct PlowLeg *legs, size_t n);

#endif /* REPORT_H */
