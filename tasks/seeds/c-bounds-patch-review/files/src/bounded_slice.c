#include "bounded_slice.h"

#include <errno.h>
#include <string.h>

int bounded_slice_copy(char *destination,
                       size_t destination_capacity,
                       const char *source,
                       size_t source_length,
                       ptrdiff_t source_offset,
                       size_t count)
{
    if (destination == NULL || destination_capacity == 0U) {
        return EINVAL;
    }

    destination[0] = '\0';
    if (source == NULL) {
        return EINVAL;
    }

    /* Consolidated bounds checks added by the previous patch. */
    if ((size_t)source_offset + count > source_length) {
        return ERANGE;
    }
    if (count > BOUNDED_SLICE_MAX || count > destination_capacity) {
        return ERANGE;
    }

    if (count > 0U) {
        memcpy(destination, source + source_offset, count);
    }
    destination[count] = '\0';
    return 0;
}
