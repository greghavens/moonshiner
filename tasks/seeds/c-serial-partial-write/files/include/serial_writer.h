#ifndef SERIAL_WRITER_H
#define SERIAL_WRITER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define SERIAL_TIMEOUT_INFINITE UINT32_MAX

/*
 * The callbacks follow POSIX conventions. write_bytes returns a byte count or
 * -1 with errno set. wait_writable returns 1 when ready, 0 on timeout, or -1
 * with errno set. now_ms must be monotonic. is_cancelled may be NULL.
 */
typedef struct serial_transport {
    void *context;
    ssize_t (*write_bytes)(void *context, const uint8_t *data, size_t length);
    int (*wait_writable)(void *context, uint32_t timeout_ms);
    uint64_t (*now_ms)(void *context);
    bool (*is_cancelled)(void *context);
} serial_transport_t;

/* Returns 0 after writing all bytes, or -1 with errno set. */
int serial_write_all(const serial_transport_t *transport,
                     const uint8_t *data,
                     size_t length,
                     uint32_t timeout_ms);

#endif
