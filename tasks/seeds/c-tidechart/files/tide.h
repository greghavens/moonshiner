#ifndef TIDE_H
#define TIDE_H

/* Tide table entry: one high- or low-water mark for the harbor. */
struct tide_entry {
    int minute;      /* minutes after midnight */
    double height_m; /* height above chart datum */
};

/* Parse a table line "HH:MM <height>" into *out (leading blanks allowed,
 * minutes always two digits). Returns 0 on success, -1 on bad input. */
int tide_parse_entry(const char *line, struct tide_entry *out);

/* Height `minute` minutes into a span between the lo and hi marks,
 * linearly interpolated; clamps to the endpoints outside the span. */
double tide_interp(double lo, double hi, int minute, int span);

#endif /* TIDE_H */
