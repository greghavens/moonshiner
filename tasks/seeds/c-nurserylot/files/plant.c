#include "plant.h"
#include "units.h"

/* Stocking summary for a bed of rows. */
struct stock_lev {
    int planted;    /* pots in the ground */
    int free_slots; /* positions still open */
    int pct_full;   /* 0..100, rounded down; empty beds count as 0 */
};

int bed_free_slots(const struct bed_row *r) {
    int open = r->slots - r->planted;

    return open > 0 ? open : 0;
}

int bed_free_total(const struct bed_row *rows, int n) {
    int total = 0;

    for (int i = 0; i < n; i++)
        total += bed_free_slots(&rows[i]);
    return total;
}

struct stock_lev plant_stock_level(const struct bed_row *rows, int n) {
    struct stock_lev lev = {0, 0, 0};
    int slots = 0;

    for (int i = 0; i < n; i++) {
        lev.planted += rows[i].planted;
        slots += rows[i].slots;
    }
    lev.free_slots = bed_free_total(rows, n);
    if (slots > 0)
        lev.pct_full = lev.planted * 100 / slots;
    return lev;
}
