/* Spreading rates per lane-km, by severity band 0..3. */
static const int rate_table[] = {8, 12, 20, 28};

static int grit_rate_for(int severity) {
    if (severity < 0)
        severity = 0;
    if (severity > 3)
        severity = 3;
    return rate_table[severity];
}

int spread_total_kg(int km, int severity) {
    if (km <= 0)
        return 0;
    return km * grit_rate_for(severity);
}
