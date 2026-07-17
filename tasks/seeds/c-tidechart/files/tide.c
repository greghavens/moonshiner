#include "tide.h"

static const char *skip_ws(const char *p) {
    while (isspace((unsigned char)*p))
        p++;
    return p;
}

int tide_parse_entry(const char *line, struct tide_entry *out) {
    const char *p = skip_ws(line);
    char *end;
    long hh, mm;

    if (!isdigit((unsigned char)*p))
        return -1;
    hh = strtol(p, &end, 10);
    if (end == p || *end != ':' || hh < 0 || hh > 23)
        return -1;
    p = end + 1;
    if (!isdigit((unsigned char)p[0]) || !isdigit((unsigned char)p[1]))
        return -1;
    mm = strtol(p, &end, 10);
    if (end != p + 2 || mm > 59)
        return -1;
    p = skip_ws(end);
    out->height_m = strtod(p, &end);
    if (end == p)
        return -1;
    out->minute = (int)(hh * 60 + mm);
    return 0;
}

double tide_interp(lo, hi, minute, span)
    double lo, hi;
    long minute;
    int span;
{
    if (span <= 0 || minute <= 0)
        return lo;
    if (minute >= span)
        return hi;
    return lo + (hi - lo) * ((double)minute / (double)span);
}

const char *tide_slot_label(double h, double lo, double hi) {
    double band = (hi - lo) / 4.0;

    if (h <= lo + band)
        return "slack-low";
    if (h >= hi - band)
        return "slack-high";
    return "moving";
}
