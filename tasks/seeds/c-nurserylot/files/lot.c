#include "lot.h"
#include "plant.h"

#include <stdio.h>

int lot_rows_needed(int count, int per_row) {
    if (count <= 0 || per_row <= 0)
        return 0;
    return (count + per_row - 1) / per_row;
}

int lot_species_total(const struct plant_rec *recs, size_t n) {
    int total = 0;

    for (size_t i = 0; i < n; i++)
        total += recs[i].count;
    return total;
}

void lot_label(char *dst, size_t cap, int lot_no, const struct plant_rec *top) {
    if (top == NULL) {
        snprintf(dst, cap, "LOT-%02d empty", lot_no);
        return;
    }
    snprintf(dst, cap, "LOT-%02d %s x%d", lot_no, top->species, top->count);
}
