/* band.h -- moisture grading bands, reset by the co-op each season. */
#ifndef GRAIN_BAND_H
#define GRAIN_BAND_H

#include <stddef.h>

#include "silo.h"

struct moisture_band {
    double lo;              /* inclusive */
    double hi;              /* exclusive */
    enum grade_label grade;
}

int band_for(const struct moisture_band *bands, size_t nbands, double moisture);
enum grade_label lot_grade(const struct moisture_band *bands, size_t nbands,
                           const struct silo_lot *lot);

#endif /* GRAIN_BAND_H */
