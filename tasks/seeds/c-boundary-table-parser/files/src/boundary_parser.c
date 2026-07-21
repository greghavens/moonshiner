#include "boundary_parser.h"

#include <string.h>

btab_status btab_parse_cell(const char *input, size_t input_len,
                            char separator, char *scratch,
                            size_t scratch_cap, btab_cell *out)
{
    size_t payload_len = 0;

    if (input == NULL || out == NULL || separator == '\0') {
        return BTAB_INVALID;
    }

    while (payload_len < input_len && input[payload_len] != '\0' &&
           input[payload_len] != separator) {
        ++payload_len;
    }

    if (payload_len == input_len) {
        return BTAB_INCOMPLETE;
    }

    if (input[payload_len] == '\0') {
        btab_cell parsed = {input, payload_len, 1};
        *out = parsed;
        return BTAB_OK;
    }

    if (scratch == NULL || scratch_cap < payload_len) {
        return BTAB_NOSPACE;
    }

    if (payload_len != 0) {
        memcpy(scratch, input, payload_len);
    }
    scratch[payload_len] = '\0';

    {
        btab_cell parsed = {scratch, payload_len, 0};
        *out = parsed;
    }
    return BTAB_OK;
}
