#include "report.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define COL_SKU 10
#define COL_DESC 24
#define COL_BIN 8
#define COL_QTY 6

/* Each column is formatted into its own cell, then the cells are
 * joined with " | ". The +1 leaves room for the terminator. */
struct cells {
    char sku[COL_SKU + 1];
    char desc[COL_DESC + 1];
    char bin[COL_BIN + 1];
    char qty[COL_QTY + 1];
};

static const char HEADER[] =
    "SKU        | DESCRIPTION              | BIN      |    QTY";

/* Left-aligned text cell: copy, clip to the column, pad with spaces. */
static void set_text(char *dst, size_t width, const char *text) {
    size_t n = strlen(text);
    if (n > width)
        n = width - 1;
    memcpy(dst, text, n);
    size_t i = n;
    while (i <= width)
        dst[i++] = ' ';
    dst[i] = '\0';
}

/* Right-aligned numeric cell; too-wide values render as ###### the
 * way the spreadsheets do. */
static void set_num(char *dst, size_t width, long value) {
    char tmp[24];
    int n = snprintf(tmp, sizeof tmp, "%ld", value);
    if (n < 0 || (size_t)n > width) {
        memset(dst, '#', width);
        dst[width] = '\0';
        return;
    }
    size_t pad = width - (size_t)n;
    memset(dst, ' ', pad);
    memcpy(dst + pad, tmp, (size_t)n);
    dst[width] = '\0';
}

static char *heap_str(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = malloc(n);
    if (p != NULL)
        memcpy(p, s, n);
    return p;
}

char *report_row(const item *it) {
    struct cells c;
    char line[160];

    set_text(c.sku, COL_SKU, it->sku);
    set_text(c.desc, COL_DESC, it->desc);
    set_text(c.bin, COL_BIN, it->bin);
    set_num(c.qty, COL_QTY, it->qty);

    snprintf(line, sizeof line, "%s | %s | %s | %s",
             c.sku, c.desc, c.bin, c.qty);
    return heap_str(line);
}

char *report_render(const item *items, size_t n) {
    size_t header_len = strlen(HEADER);
    char **rows = NULL;
    if (n > 0) {
        rows = calloc(n, sizeof *rows);
        if (rows == NULL)
            return NULL;
    }

    /* Render every row first so the output buffer can be sized to
     * whatever the rows actually are. */
    size_t total = 2 * (header_len + 1); /* header + rule, each + '\n' */
    for (size_t i = 0; i < n; i++) {
        rows[i] = report_row(&items[i]);
        if (rows[i] == NULL) {
            for (size_t j = 0; j < i; j++)
                free(rows[j]);
            free(rows);
            return NULL;
        }
        total += strlen(rows[i]) + 1;
    }

    char *out = malloc(total + 1);
    if (out == NULL) {
        for (size_t i = 0; i < n; i++)
            free(rows[i]);
        free(rows);
        return NULL;
    }

    char *w = out;
    memcpy(w, HEADER, header_len);
    w += header_len;
    *w++ = '\n';
    memset(w, '-', header_len);
    w += header_len;
    *w++ = '\n';
    for (size_t i = 0; i < n; i++) {
        size_t len = strlen(rows[i]);
        memcpy(w, rows[i], len);
        w += len;
        *w++ = '\n';
        free(rows[i]);
    }
    *w = '\0';
    free(rows);
    return out;
}
