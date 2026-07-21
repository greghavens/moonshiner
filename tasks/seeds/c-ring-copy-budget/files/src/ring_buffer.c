#include "ring_buffer.h"

#include <stdlib.h>
#include <string.h>

static size_t smaller(size_t left, size_t right)
{
    return left < right ? left : right;
}

static void reader_move(RingBuffer *ring, uint8_t *destination,
                        const uint8_t *source, size_t length)
{
    memcpy(destination, source, length);
    ring->reader_bytes_moved += length;
}

bool ring_buffer_init(RingBuffer *ring, uint8_t *storage, size_t capacity)
{
    if (ring == NULL || storage == NULL || capacity == 0U) {
        return false;
    }

    ring->storage = storage;
    ring->capacity = capacity;
    ring->read_index = 0U;
    ring->write_index = 0U;
    ring->used = 0U;
    ring->reader_bytes_moved = 0U;
    return true;
}

size_t ring_buffer_size(const RingBuffer *ring)
{
    return ring->used;
}

size_t ring_buffer_write(RingBuffer *ring, const uint8_t *source, size_t length)
{
    size_t writable;
    size_t first;
    size_t second;

    if (length == 0U || source == NULL) {
        return 0U;
    }

    writable = smaller(length, ring->capacity - ring->used);
    first = smaller(writable, ring->capacity - ring->write_index);
    second = writable - first;

    memcpy(ring->storage + ring->write_index, source, first);
    if (second != 0U) {
        memcpy(ring->storage, source + first, second);
    }

    ring->write_index = (ring->write_index + writable) % ring->capacity;
    ring->used += writable;
    return writable;
}

size_t ring_buffer_read(RingBuffer *ring, uint8_t *destination, size_t length)
{
    size_t readable;
    size_t first;
    size_t second;
    uint8_t *staging;

    if (length == 0U || ring->used == 0U) {
        return 0U;
    }
    if (destination == NULL) {
        return 0U;
    }

    readable = smaller(length, ring->used);
    staging = malloc(readable);
    if (staging == NULL) {
        return 0U;
    }

    first = smaller(readable, ring->capacity - ring->read_index);
    second = readable - first;
    reader_move(ring, staging, ring->storage + ring->read_index, first);
    if (second != 0U) {
        reader_move(ring, staging + first, ring->storage, second);
    }
    reader_move(ring, destination, staging, readable);
    free(staging);

    ring->read_index = (ring->read_index + readable) % ring->capacity;
    ring->used -= readable;
    return readable;
}

void ring_buffer_reset_reader_bytes_moved(RingBuffer *ring)
{
    ring->reader_bytes_moved = 0U;
}

size_t ring_buffer_reader_bytes_moved(const RingBuffer *ring)
{
    return ring->reader_bytes_moved;
}
