#include "summary.h"

#include "route.h"
#include "spread.h"

#include <stdio.h>

void grit_summary_line(char *dst, size_t cap, const struct leg *legs, int n) {
    int worst = 0;

    for (int i = 0; i < n; i++)
        if (legs[i].severity > worst)
            worst = legs[i].severity;
    snprintf(dst, cap, "legs=%d km=%d plan=%dkg reserve=%dkg worst-rate=%d",
             n, route_leg_total(legs, n), route_plan_kg(legs, n),
             route_reserve_after(legs, n), grit_rate_for(worst));
}
