#ifndef RING_BUFFER_H
#define RING_BUFFER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t *storage;
    size_t capacity;
    size_t read_index;
    size_t write_index;
    size_t used;
    size_t reader_bytes_moved;
} RingBuffer;

/* The caller retains ownership of storage for the lifetime of the ring. */
bool ring_buffer_init(RingBuffer *ring, uint8_t *storage, size_t capacity);

size_t ring_buffer_size(const RingBuffer *ring);
size_t ring_buffer_write(RingBuffer *ring, const uint8_t *source, size_t length);

/*
 * Copies up to length bytes into destination and returns the number copied.
 * The caller retains ownership of destination; the ring never retains it.
 * destination may be NULL when length is zero.
 */
size_t ring_buffer_read(RingBuffer *ring, uint8_t *destination, size_t length);

void ring_buffer_reset_reader_bytes_moved(RingBuffer *ring);
size_t ring_buffer_reader_bytes_moved(const RingBuffer *ring);

#endif
