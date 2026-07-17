#ifndef VANLOG_H
#define VANLOG_H

#include <stddef.h>

/* One entry from a refrigerated van's cold-chain recorder. */
struct VanRec {
    int minute;          /* minutes since departure */
    double temp_c;       /* box temperature when the entry was logged */
    unsigned char kind;  /* 'T' reading, 'D' door opened, 'A' recorder alert */
};

struct VanSummary {
    double min_c;          /* coldest temp-bearing entry ('T' or 'A') */
    double max_c;          /* warmest temp-bearing entry */
    double worst_alert_c;  /* warmest 'A' entry, 0 when no alerts */
    int readings;          /* count of 'T' entries */
    int alerts;            /* count of 'A' entries */
    int events;            /* door openings plus alerts */
};

/* Load a trip file: a size_t entry count, then that many raw VanRec
 * entries. Returns the number of entries read, or -1 when the file is
 * short, damaged, or holds more than cap entries. */
long vanlog_load(const char *path, struct VanRec *out, size_t cap);

struct VanSummary vanlog_summarize(const struct VanRec *recs, size_t n);

#endif /* VANLOG_H */
