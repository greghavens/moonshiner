#ifndef CHART_H
#define CHART_H

#include <stddef.h>

/* Render one chart row into dst: berth name padded/cut to 10 columns,
 * height to two decimals, then the water-state tag for that height. */
void tide_render_row(char *dst, size_t cap, const char *name,
                     double h, double lo, double hi);

#endif /* CHART_H */
