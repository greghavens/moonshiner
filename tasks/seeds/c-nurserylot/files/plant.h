#ifndef PLANT_H
#define PLANT_H

/* One species entry on a lot's planting sheet. */
struct plant_rec {
    const char *species;
    int count; /* pots recorded for the species */
};

/* Free pot positions left in one bed row (never negative). */
int bed_free_slots(const struct bed_row *r);

/* Free positions summed across the rows of a bed. */
int bed_free_total(const struct bed_row *rows, int n);

/* Stocking summary for a bed: totals plus how full it is. */
struct stock_lev plant_stock_level(const struct bed_row *rows, int n);

#endif /* PLANT_H */
