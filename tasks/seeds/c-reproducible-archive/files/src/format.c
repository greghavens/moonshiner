#include "beacon.h"

#include <stdio.h>

int
beacon_format_record(char *out, size_t capacity, uint32_t sequence,
                     const char *message)
{
    char encoded[9];

    (void)beacon_encode_u32(sequence, encoded);
    return snprintf(out, capacity, "%s:%s", encoded, message);
}
