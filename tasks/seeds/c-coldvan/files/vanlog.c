#include <stdio.h>

#include "vanlog.h"

long vanlog_load(const char *path, struct VanRec *out, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f)
        return -1;
    size_t count = 0;
    fread(&count, sizeof count, 1, f);
    if (count > cap) {
        fclose(f);
        return -1;
    }
    size_t got = fread(out, sizeof *out, count, f);
    fclose(f);
    if (got != count)
        return -1;
    return (long)count;
}

struct VanSummary vanlog_summarize(const struct VanRec *recs, size_t n) {
    struct VanSummary s = {0};
    double lo, hi;
    int first = 1;
    for (size_t i = 0; i < n; i++) {
        double t = recs[i].temp_c;
        switch (recs[i].kind) {
        case 'A': {
            double t = recs[i].temp_c;
            s.alerts++;
            if (t > s.worst_alert_c)
                s.worst_alert_c = t;
        }
        case 'D':
            s.events++;
            break;
        case 'T':
            s.readings++;
            break;
        default:
            break;
        }
        if (recs[i].kind == 'T' || recs[i].kind == 'A') {
            if (first || t < lo)
                lo = t;
            if (first || t > hi)
                hi = t;
            first = 0;
        }
    }
    s.min_c = lo;
    s.max_c = hi;
    return s;
}
