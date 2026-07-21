#ifndef MAPPED_SLICE_H
#define MAPPED_SLICE_H

#include <stddef.h>
#include <stdint.h>

typedef enum mapped_slice_status {
    MAPPED_SLICE_OK = 0,
    MAPPED_SLICE_INVALID,
    MAPPED_SLICE_RANGE,
    MAPPED_SLICE_SYSTEM
} mapped_slice_status;

/*
 * Copy exactly length bytes beginning at offset from path into destination.
 *
 * A zero-length request accepts a null destination.  Its offset may equal the
 * file size, but may not exceed it.  Invalid arguments, ranges outside the
 * file, and failures before the copy leave destination unchanged.
 */
mapped_slice_status mapped_slice_read(const char *path,
                                      uint64_t offset,
                                      size_t length,
                                      unsigned char *destination);

#endif
