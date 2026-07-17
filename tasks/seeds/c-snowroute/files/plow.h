#ifndef PLOW_H
#define PLOW_H

#include <stddef.h>

struct PlowLeg {
    const char *street;
    long passes;      /* plow passes completed this shift */
    size_t salt_kg;   /* salt spread on this leg */
    double lane_km;   /* lane-kilometres cleared */
};

/* Filters receive the current shift mask (bit per shift) so dispatch can
 * reuse one signature for every rule. */
typedef int (*leg_filter)(const struct PlowLeg *leg, unsigned shift_mask);

int filter_any(const struct PlowLeg *leg, unsigned shift_mask);

size_t route_salt_total(const struct PlowLeg *legs, size_t n);
double route_km_total(const struct PlowLeg *legs, size_t n);
long route_pass_total(const struct PlowLeg *legs, size_t n);

/* 1 when the shift's salt use exceeds one hopper load, else 0. */
int route_needs_refill(const struct PlowLeg *legs, size_t n, size_t hopper_kg);

#endif /* PLOW_H */
