/* sweep.h — archive-candidate sweep over a collector listing.
 *
 * The nightly collector walks a share and emits one line per file:
 *
 *     <bytes>\t<path>
 *
 * with <bytes> = -1 when the file could not be statted. sweep_parse
 * ingests a listing; sweep_render writes the candidate report: every
 * stored file of at least min_bytes, biggest first.
 */
#ifndef SWEEP_H
#define SWEEP_H

#include <stddef.h>

#define SWEEP_MAX_FILES 32
#define SWEEP_PATH_MAX 64

typedef struct {
    char path[SWEEP_PATH_MAX];
    unsigned size; /* bytes */
} sweep_ent;

typedef struct {
    sweep_ent files[SWEEP_MAX_FILES];
    size_t count;   /* stored entries */
    size_t skipped; /* unreadable (-1) lines in the listing */
} sweep_scan;

/* Parse a listing into s. Returns the number of stored entries, or -1
 * on a malformed line (no tab, no leading number, empty or oversized
 * path) or when the listing exceeds SWEEP_MAX_FILES stored entries. */
int sweep_parse(sweep_scan *s, const char *listing);

/* Write the report for files with size >= min_bytes into out: one line
 * "<MiB> MiB\t<path>\n" per candidate, sizes in whole MiB (rounded
 * down), biggest first, equal sizes ordered by path. Returns the number
 * of report lines, or -1 on bad arguments or when out is too small. */
int sweep_render(const sweep_scan *s, long long min_bytes, char *out,
                 size_t cap);

#endif /* SWEEP_H */
