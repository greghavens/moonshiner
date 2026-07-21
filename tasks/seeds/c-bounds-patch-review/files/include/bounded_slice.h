#ifndef BOUNDED_SLICE_H
#define BOUNDED_SLICE_H

#include <stddef.h>

#define BOUNDED_SLICE_MAX ((size_t)16)

/*
 * Copy count bytes starting at source_offset into destination and append a NUL.
 *
 * Returns:
 *   0       success
 *   EINVAL  invalid pointer, zero destination capacity, or negative offset
 *   ERANGE  count, source range, or destination capacity is out of range
 *
 * If destination is non-NULL and destination_capacity is nonzero, all errors
 * leave destination as an empty string.
 */
int bounded_slice_copy(char *destination,
                       size_t destination_capacity,
                       const char *source,
                       size_t source_length,
                       ptrdiff_t source_offset,
                       size_t count);

#endif
