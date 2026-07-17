#include <stdio.h>

#include "report.h"

/* Label row appended after the per-leg rows; counts come from plow.c. */
static const struct PlowLeg TOTALS_ROW = { "TOTALS", 0 };

int leg_line(char *out, size_t cap, const struct PlowLeg *leg) {
    return snprintf(out, cap, "%-14s %3d passes %5d kg %6.2f km",
                    leg->street, leg->passes, leg->salt_kg, leg->lane_km);
}

int report_render(char *out, size_t cap,
                  const struct PlowLeg *legs, size_t n) {
    size_t used = 0;
    int rows = 0;
    for (size_t i = 0; i < n; i++) {
        int w = leg_line(out + used, cap - used, &legs[i]);
        if (w < 0 || used + (size_t)w + 1 >= cap)
            return rows;
        used += (size_t)w;
        out[used++] = '\n';
        rows++;
    }
    int w = snprintf(out + used, cap - used,
                     "%-14s %3d passes %5u kg %6.2f km\n",
                     TOTALS_ROW.street,
                     route_pass_total(legs, n),
                     route_salt_total(legs, n),
                     route_km_total(legs, n));
    if (w < 0 || (size_t)w >= cap - used)
        return rows;
    return rows + 1;
}
