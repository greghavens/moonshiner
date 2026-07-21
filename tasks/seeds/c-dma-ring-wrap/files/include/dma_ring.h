#ifndef DMA_RING_H
#define DMA_RING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct dma_ring_registers {
    volatile uint32_t producer;
    volatile uint32_t consumer;
};

struct dma_span {
    uint8_t *data;
    size_t length;
};

struct dma_ring_view {
    struct dma_span first;
    struct dma_span second;
};

struct dma_ring {
    uint8_t *storage;
    uint32_t capacity;
    struct dma_ring_registers *registers;
};

bool dma_ring_init(struct dma_ring *ring,
                   uint8_t *storage,
                   uint32_t capacity,
                   struct dma_ring_registers *registers);

size_t dma_ring_consumer_view(const struct dma_ring *ring,
                              struct dma_ring_view *view);
size_t dma_ring_producer_view(const struct dma_ring *ring,
                              struct dma_ring_view *view);

bool dma_ring_consume(struct dma_ring *ring, uint32_t count);
bool dma_ring_produce(struct dma_ring *ring, uint32_t count);

#endif
