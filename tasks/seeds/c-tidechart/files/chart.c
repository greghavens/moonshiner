#include "chart.h"
#include "tide.h"

#include <stdio.h>

void tide_render_row(char *dst, size_t cap, const char *name,
                     double h, double lo, double hi) {
    char label[11];
    size_t n = strlen(name);
    const char *state = tide_slot_label(h, lo, hi);

    if (n > sizeof label - 1)
        n = sizeof label - 1;
    memset(label, ' ', sizeof label - 1);
    memcpy(label, name, n);
    label[sizeof label - 1] = '\0';
    snprintf(dst, cap, "%s | %5.2fm | %s", label, h, state);
}
