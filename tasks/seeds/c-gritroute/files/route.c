#include "route.h"
#include "spread.h"

int route_leg_totals(const struct leg *legs, int n) {
    int km = 0;

    for (int i = 0; i < n; i++)
        km += legs[i].km;
    return km;
}

int route_plan_kg(const struct leg *legs, int n) {
    int kg = 0;

    for (int i = 0; i < n; i++)
        kg += spread_total_kg(legs[i].km, legs[i].severity);
    return kg;
}

int route_reserve_after(const struct leg *legs, int n) {
    return grit_reserve_kg - route_plan_kg(legs, n);
}
