#define _POSIX_C_SOURCE 200809L

#include "mapped_slice.h"

#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static mapped_slice_status close_and_return(int descriptor,
                                            mapped_slice_status status)
{
    if (close(descriptor) != 0) {
        return MAPPED_SLICE_SYSTEM;
    }
    return status;
}

mapped_slice_status mapped_slice_read(const char *path,
                                      uint64_t offset,
                                      size_t length,
                                      unsigned char *destination)
{
    if (path == NULL || (length != 0U && destination == NULL)) {
        return MAPPED_SLICE_INVALID;
    }

    int descriptor = open(path, O_RDONLY);
    if (descriptor < 0) {
        return MAPPED_SLICE_SYSTEM;
    }

    struct stat file_info;
    if (fstat(descriptor, &file_info) != 0) {
        return close_and_return(descriptor, MAPPED_SLICE_SYSTEM);
    }
    if (file_info.st_size < 0) {
        return close_and_return(descriptor, MAPPED_SLICE_RANGE);
    }

    const uintmax_t file_size = (uintmax_t)file_info.st_size;
    const uintmax_t requested_offset = (uintmax_t)offset;
    if (requested_offset > file_size ||
        (uintmax_t)length > file_size - requested_offset) {
        return close_and_return(descriptor, MAPPED_SLICE_RANGE);
    }

    if (length == 0U) {
        return close_and_return(descriptor, MAPPED_SLICE_OK);
    }

    const long page_value = sysconf(_SC_PAGE_SIZE);
    if (page_value <= 0) {
        return close_and_return(descriptor, MAPPED_SLICE_SYSTEM);
    }

    const uintmax_t page_size = (uintmax_t)page_value;
    const uintmax_t page_prefix_wide = requested_offset % page_size;
    if (page_prefix_wide > (uintmax_t)SIZE_MAX) {
        return close_and_return(descriptor, MAPPED_SLICE_RANGE);
    }

    const size_t page_prefix = (size_t)page_prefix_wide;
    if (length > SIZE_MAX - page_prefix) {
        return close_and_return(descriptor, MAPPED_SLICE_RANGE);
    }

    const uintmax_t aligned_offset_wide =
        requested_offset - page_prefix_wide;
    const off_t aligned_offset = (off_t)aligned_offset_wide;

    /* The mapping begins at aligned_offset, before the requested bytes. */
    const size_t mapping_length = length;
    void *mapping = mmap(NULL, mapping_length, PROT_READ, MAP_PRIVATE,
                         descriptor, aligned_offset);
    if (mapping == MAP_FAILED) {
        return close_and_return(descriptor, MAPPED_SLICE_SYSTEM);
    }

    if (close(descriptor) != 0) {
        (void)munmap(mapping, mapping_length);
        return MAPPED_SLICE_SYSTEM;
    }

    const unsigned char *source =
        (const unsigned char *)mapping + page_prefix;
    memcpy(destination, source, length);

    if (munmap(mapping, mapping_length) != 0) {
        return MAPPED_SLICE_SYSTEM;
    }
    return MAPPED_SLICE_OK;
}
