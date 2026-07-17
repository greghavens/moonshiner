/* grade.c -- grading math for the elevator intake board. */
#include "band.h"
#include "silo.h"

double total_tons(const struct silo_lot *lots, size_t nlots) {
    double sum = 0.0;
    for (size_t i = 0; i < nlots; i++)
        sum += lots[i].tons;
    return sum;
}

double blend_moisture(const struct silo_lot *lots, size_t nlots) {
    double tons = total_tons(lots, nlots);
    if (tons <= 0.0)
        return 0.0;
    double weighted = 0.0;
    for (size_t i = 0; i < nlots; i++)
        weighted += lots[i].moisture * lots[i].tons;
    return weighted / tons;
}

int band_for(const struct moisture_band *bands, size_t nbands, double moisture) {
    for (size_t i = 0; i < nbands; i++) {
        if (moisture >= bands[i].lo && moisture < bands[i].hi)
            return (int)i;
    }
    return -1;
}

enum grade_label lot_grade(const struct moisture_band *bands, size_t nbands,
                           const struct silo_lot *lot) {
    int idx = band_for(bands, nbands, lot->moisture);
    if (idx < 0)
        return GRADE_REJECT;
    return bands[idx].grade;
}
