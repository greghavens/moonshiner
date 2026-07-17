#include "sweep.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --- listing parser -------------------------------------------------- */

int sweep_parse(sweep_scan *s, const char *listing) {
    if (s == NULL || listing == NULL)
        return -1;
    s->count = 0;
    s->skipped = 0;

    const char *p = listing;
    while (*p != '\0') {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        const char *next = nl ? nl + 1 : p + len;
        if (len == 0) {
            p = next;
            continue;
        }

        char *end = NULL;
        long long bytes = strtoll(p, &end, 10);
        if (end == p || end >= p + len || *end != '\t')
            return -1;
        const char *path = end + 1;
        size_t plen = (size_t)(p + len - path);
        if (plen == 0 || plen >= SWEEP_PATH_MAX)
            return -1;

        if (bytes < 0) {
            /* collector could not stat this one */
            s->skipped++;
            p = next;
            continue;
        }
        if (s->count == SWEEP_MAX_FILES)
            return -1;
        sweep_ent *e = &s->files[s->count++];
        memcpy(e->path, path, plen);
        e->path[plen] = '\0';
        e->size = bytes;
        p = next;
    }
    return (int)s->count;
}

/* --- report ----------------------------------------------------------- */

/* Biggest first; equal sizes fall back to path order so reruns come out
 * identical. */
static int size_order(const sweep_ent *a, const sweep_ent *b) {
    if (a->size != b->size)
        return (int)(b->size - a->size);
    return strcmp(a->path, b->path);
}

int sweep_render(const sweep_scan *s, long long min_bytes, char *out,
                 size_t cap) {
    if (s == NULL || out == NULL || cap == 0)
        return -1;

    int idx[SWEEP_MAX_FILES];
    int k = 0;
    for (size_t i = 0; i < s->count; i++)
        if (s->files[i].size >= min_bytes)
            idx[k++] = (int)i;

    /* stable insertion sort over the candidate indexes */
    for (int i = 1; i < k; i++) {
        int t = idx[i];
        int j = i;
        while (j > 0 && size_order(&s->files[idx[j - 1]], &s->files[t]) > 0) {
            idx[j] = idx[j - 1];
            j--;
        }
        idx[j] = t;
    }

    size_t off = 0;
    out[0] = '\0';
    for (int i = 0; i < k; i++) {
        const sweep_ent *e = &s->files[idx[i]];
        long long mib = e->size / (1024 * 1024);
        int n = snprintf(out + off, cap - off, "%lld MiB\t%s\n", mib,
                         e->path);
        if (n < 0 || (size_t)n >= cap - off)
            return -1;
        off += (size_t)n;
    }
    return k;
}
