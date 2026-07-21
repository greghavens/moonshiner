#ifndef BOUNDARY_PARSER_H
#define BOUNDARY_PARSER_H

#include <stddef.h>

typedef enum {
    BTAB_OK = 0,
    BTAB_INCOMPLETE,
    BTAB_NOSPACE,
    BTAB_INVALID
} btab_status;

typedef struct {
    const char *data;
    size_t length;
    int borrowed;
} btab_cell;

/*
 * Parse the first cell in input[0..input_len).
 *
 * A NUL-terminated cell borrows input.  A separator-terminated cell is copied
 * to scratch and receives a trailing NUL.  On failure, out and scratch are
 * unchanged.
 */
btab_status btab_parse_cell(const char *input, size_t input_len,
                            char separator, char *scratch,
                            size_t scratch_cap, btab_cell *out);

#endif
