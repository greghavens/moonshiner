/* vent.c -- ridge-vent positioning for the seedling house controller. */
#include "vent.h"

static double clampd(double v, double lo, double hi) {
    if (v < lo)
        return lo;
    if (v > hi)
        return hi;
    return v;
}

int vent_target_percent(double temp_c, double setpoint_c, double band_c) {
    if (band_c <= 0.0)
        return temp_c > setpoint_c ? 100 : 0;
    double frac = clampd((temp_c - setpoint_c) / band_c, 0.0, 1.0);
    return (int)(frac * 100.0 + 0.5);
}

#ifdef VENT_TRACE
#include <stdio.h>
static void trace_move(int from, int to) {
    fprintf(stderr, "vent: %d -> %d\n", from, to);
}

int vent_step_toward(int current, int target, int max_step) {
    int next = current;
    if (max_step < 1)
        max_step = 1;
    if (target > current) {
        next = current + max_step;
        if (next > target)
            next = target;
    } else if (target < current) {
        next = current - max_step;
        if (next < target)
            next = target;
    }
#ifdef VENT_TRACE
    trace_move(current, next);
#endif
    return next;
}

int vent_plan(double temp_c, double setpoint_c, double band_c,
              int current, int max_step, int steps[], int cap) {
    int target = vent_target_percent(temp_c, setpoint_c, band_c);
    int used = 0;
    while (current != target && used < cap) {
        current = vent_step_toward(current, target, max_step);
        steps[used++] = current;
    }
    return used;
}
#endif /* VENT_TRACE */
