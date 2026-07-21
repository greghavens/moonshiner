#include "snapshot_reader.h"

#include <errno.h>
#include <unistd.h>

int snapshot_read_exact(int fd, unsigned char *destination, size_t length)
{
    size_t received = 0;

    if (length == 0) {
        return 0;
    }
    if (destination == NULL) {
        errno = EINVAL;
        return -1;
    }

    while (received < length) {
        ssize_t result = read(fd,
                              destination + received,
                              length - received);

        if (result > 0) {
            received += (size_t)result;
            continue;
        }
        if (result == 0) {
            errno = ECONNRESET;
            return -1;
        }
        if (errno == EINTR) {
            /* Restart the snapshot after an interrupted system call. */
            received = 0;
            continue;
        }
        return -1;
    }

    return 0;
}
