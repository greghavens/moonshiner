#include "serial_writer.h"

#include <errno.h>

int serial_write_all(const serial_transport_t *transport,
                     const uint8_t *data,
                     size_t length,
                     uint32_t timeout_ms)
{
    ssize_t written;

    if (transport == NULL || transport->write_bytes == NULL ||
        transport->wait_writable == NULL || transport->now_ms == NULL ||
        (data == NULL && length != 0U)) {
        errno = EINVAL;
        return -1;
    }

    if (length == 0U) {
        return 0;
    }

    if (transport->is_cancelled != NULL &&
        transport->is_cancelled(transport->context)) {
        errno = ECANCELED;
        return -1;
    }

    (void)timeout_ms;
    written = transport->write_bytes(transport->context, data, length);
    if (written < 0) {
        return -1;
    }
    if ((size_t)written != length) {
        errno = EIO;
        return -1;
    }

    return 0;
}
