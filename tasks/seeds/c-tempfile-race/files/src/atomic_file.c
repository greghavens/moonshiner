#define _POSIX_C_SOURCE 200809L

#include "atomic_file.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int create_temporary_file(const char *destination, char **temporary_path)
{
    const size_t suffix_capacity = 64U;
    size_t destination_length;
    size_t capacity;
    char *candidate;
    int written;
    int descriptor;

    destination_length = strlen(destination);
    if (destination_length > SIZE_MAX - suffix_capacity) {
        return ENAMETOOLONG;
    }

    capacity = destination_length + suffix_capacity;
    candidate = malloc(capacity);
    if (candidate == NULL) {
        return ENOMEM;
    }

    written = snprintf(candidate, capacity, "%s.tmp.%ld", destination,
                       (long)getpid());
    if (written < 0 || (size_t)written >= capacity) {
        free(candidate);
        return EOVERFLOW;
    }

    descriptor = open(candidate, O_WRONLY | O_CREAT | O_TRUNC,
                      S_IRUSR | S_IWUSR);
    if (descriptor < 0) {
        int error = errno;

        free(candidate);
        return error;
    }

    *temporary_path = candidate;
    return descriptor;
}

static int write_all(int descriptor, const void *data, size_t length)
{
    const unsigned char *cursor = data;
    size_t remaining = length;

    while (remaining > 0U) {
        size_t chunk = remaining;
        ssize_t written;

        if (chunk > (size_t)SSIZE_MAX) {
            chunk = (size_t)SSIZE_MAX;
        }

        written = write(descriptor, cursor, chunk);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return errno;
        }
        if (written == 0) {
            return EIO;
        }

        cursor += (size_t)written;
        remaining -= (size_t)written;
    }

    return 0;
}

int atomic_file_write(const char *destination, const void *data, size_t length,
                      mode_t permissions)
{
    char *temporary_path = NULL;
    mode_t published_permissions;
    int descriptor;
    int result;

    if (destination == NULL || (data == NULL && length != 0U)) {
        return EINVAL;
    }

    descriptor = create_temporary_file(destination, &temporary_path);
    if (descriptor < 0) {
        return descriptor;
    }

    result = write_all(descriptor, data, length);
    published_permissions =
        permissions & (mode_t)(S_IRWXU | S_IRWXG | S_IRWXO);

    if (result == 0 && fchmod(descriptor, published_permissions) != 0) {
        result = errno;
    }
    if (result == 0 && fsync(descriptor) != 0) {
        result = errno;
    }
    if (close(descriptor) != 0 && result == 0) {
        result = errno;
    }

    if (result == 0 && rename(temporary_path, destination) != 0) {
        result = errno;
    }

    if (result != 0) {
        (void)unlink(temporary_path);
    }
    free(temporary_path);
    return result;
}
