/* silo.h -- lot bookkeeping for the elevator intake board. */
#ifndef GRAIN_SILO_H
#define GRAIN_SILO_H_

#include <stddef.h>

enum grade_label {
    GRADE_PRIME = 0,
    GRADE_STANDARD = 1,
    GRADE_FEED = 2,
    GRADE_REJECT = 3
};

struct silo_lot {
    char bin[8];        /* intake bin id, e.g. "B-104" */
    double tons;
    double moisture;    /* percent, as sampled at the probe */
};

double total_tons(const struct silo_lot *lots, size_t nlots);
double blend_moisture(const struct silo_lot *lots, size_t nlots);

#endif /* GRAIN_SILO_H */
