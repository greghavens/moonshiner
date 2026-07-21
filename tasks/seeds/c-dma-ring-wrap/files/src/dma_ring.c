#include "dma_ring.h"

#include <stdatomic.h>

static void clear_view(struct dma_ring_view *view)
{
    view->first.data = NULL;
    view->first.length = 0U;
    view->second.data = NULL;
    view->second.length = 0U;
}

static void load_cursors(const struct dma_ring *ring,
                         uint32_t *producer,
                         uint32_t *consumer)
{
    *consumer = ring->registers->consumer;
    *producer = ring->registers->producer;
    atomic_thread_fence(memory_order_acquire);
}

static uint32_t used_entries(uint32_t producer,
                             uint32_t consumer,
                             uint32_t capacity)
{
    uint64_t used = (uint64_t)producer - (uint64_t)consumer;

    /* Treat an impossible snapshot as unavailable rather than overrunning. */
    return used > capacity ? capacity : (uint32_t)used;
}

static size_t make_view(const struct dma_ring *ring,
                        uint32_t cursor,
                        uint32_t count,
                        struct dma_ring_view *view)
{
    size_t offset;
    size_t first_length;

    clear_view(view);
    if (count == 0U) {
        return 0U;
    }

    offset = (size_t)(cursor & (ring->capacity - 1U));
    first_length = (size_t)ring->capacity - offset;
    if (first_length > count) {
        first_length = count;
    }

    view->first.data = ring->storage + offset;
    view->first.length = first_length;
    if ((size_t)count > first_length) {
        view->second.data = ring->storage;
        view->second.length = (size_t)count - first_length;
    }

    return count;
}

bool dma_ring_init(struct dma_ring *ring,
                   uint8_t *storage,
                   uint32_t capacity,
                   struct dma_ring_registers *registers)
{
    if (ring == NULL || storage == NULL || registers == NULL ||
        capacity == 0U || (capacity & (capacity - 1U)) != 0U) {
        return false;
    }

    ring->storage = storage;
    ring->capacity = capacity;
    ring->registers = registers;
    return true;
}

size_t dma_ring_consumer_view(const struct dma_ring *ring,
                              struct dma_ring_view *view)
{
    uint32_t producer;
    uint32_t consumer;
    uint32_t used;

    if (ring == NULL || view == NULL) {
        return 0U;
    }

    load_cursors(ring, &producer, &consumer);
    used = used_entries(producer, consumer, ring->capacity);
    return make_view(ring, consumer, used, view);
}

size_t dma_ring_producer_view(const struct dma_ring *ring,
                              struct dma_ring_view *view)
{
    uint32_t producer;
    uint32_t consumer;
    uint32_t used;

    if (ring == NULL || view == NULL) {
        return 0U;
    }

    load_cursors(ring, &producer, &consumer);
    used = used_entries(producer, consumer, ring->capacity);
    return make_view(ring, producer, ring->capacity - used, view);
}

bool dma_ring_consume(struct dma_ring *ring, uint32_t count)
{
    uint32_t producer;
    uint32_t consumer;

    if (ring == NULL) {
        return false;
    }

    load_cursors(ring, &producer, &consumer);
    if (count > used_entries(producer, consumer, ring->capacity)) {
        return false;
    }

    atomic_thread_fence(memory_order_release);
    ring->registers->consumer = consumer + count;
    return true;
}

bool dma_ring_produce(struct dma_ring *ring, uint32_t count)
{
    uint32_t producer;
    uint32_t consumer;
    uint32_t free_entries;

    if (ring == NULL) {
        return false;
    }

    load_cursors(ring, &producer, &consumer);
    free_entries = ring->capacity -
                   used_entries(producer, consumer, ring->capacity);
    if (count > free_entries) {
        return false;
    }

    atomic_thread_fence(memory_order_release);
    ring->registers->producer = producer + count;
    return true;
}
