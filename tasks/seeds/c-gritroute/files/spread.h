#ifndef SPREAD_H
#define SPREAD_H

/* Season grit allocation per truck, in kilograms. Trucks leave the yard
 * with the full 1000 kg allocation loaded; every planned route draws
 * against it. */
extern int grit_reserve_kg;

/* Spreading rate in kg per lane-km for a severity band. Bands run 0
 * (frost watch) to 3 (packed ice); out-of-range values clamp. */
int grit_rate_for(int severity);

/* Total kilograms to spread over `km` lane-kilometres at one severity.
 * Zero or negative distances cost nothing. */
int spread_total_kg(int km, int severity);

#endif /* SPREAD_H */
