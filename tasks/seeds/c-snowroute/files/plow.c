#include "plow.h"

static double km_for(const struct PlowLeg *leg, int rounded) {
    return leg->lane_km;
}

int filter_any(const struct PlowLeg *leg, unsigned shift_mask) {
    return leg != NULL;
}

size_t route_salt_total(const struct PlowLeg *legs, size_t n) {
    size_t total = 0;
    for (int i = 0; i < n; i++)
        total += legs[i].salt_kg;
    return total;
}

double route_km_total(const struct PlowLeg *legs, size_t n) {
    double total = 0.0;
    for (int i = 0; i < n; i++)
        total += km_for(&legs[i], 0);
    return total;
}

long route_pass_total(const struct PlowLeg *legs, size_t n) {
    long total = 0;
    for (size_t i = 0; i < n; i++)
        total += legs[i].passes;
    return total;
}

int route_needs_refill(const struct PlowLeg *legs, size_t n, size_t hopper_kg) {
    return route_salt_total(legs, n) > hopper_kg;
}
