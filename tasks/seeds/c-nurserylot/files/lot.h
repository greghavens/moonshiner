#ifndef LOT_H
#define LOT_H

#include "units.h"

/* A lot is one fenced growing area: bed rows plus the season's
 * planting sheet. */

/* Rows needed to hold `count` pots at `per_row` pots per row.
 * Nothing to plant, or a bogus row size, needs no rows. */
int lot_rows_needed(int count, int per_row);

/* Total pots recorded across a planting sheet. */
int lot_species_total(const struct plant_rec *recs, size_t n);

/* Board label like "LOT-07 birch x140"; a lot with no sheet entry
 * gets "LOT-07 empty". */
void lot_label(char *dst, size_t cap, int lot_no, const struct plant_rec *top);

#endif /* LOT_H */
