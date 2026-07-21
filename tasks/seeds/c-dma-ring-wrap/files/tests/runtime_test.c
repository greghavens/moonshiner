#include "dma_ring.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

static unsigned int failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            fprintf(stderr, "check failed at line %d: %s\n", __LINE__,        \
                    #condition);                                                \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

struct view_expectation {
    uint32_t producer;
    uint32_t consumer;
    size_t readable;
    size_t read_first;
    size_t read_second;
    size_t read_offset;
    size_t writable;
    size_t write_first;
    size_t write_second;
    size_t write_offset;
};

static void check_view(const struct dma_ring_view *view,
                       uint8_t *storage,
                       size_t total,
                       size_t first,
                       size_t second,
                       size_t offset)
{
    CHECK(view->first.length == first);
    CHECK(view->second.length == second);
    CHECK(first == 0U ? view->first.data == NULL
                      : view->first.data == storage + offset);
    CHECK(second == 0U ? view->second.data == NULL
                       : view->second.data == storage);
    CHECK(view->first.length + view->second.length == total);
}

static void test_scripted_views(struct dma_ring *ring,
                                struct dma_ring_registers *registers,
                                uint8_t *storage)
{
    static const struct view_expectation script[] = {
        {0U, 0U, 0U, 0U, 0U, 0U, 8U, 8U, 0U, 0U},
        {8U, 0U, 8U, 8U, 0U, 0U, 0U, 0U, 0U, 0U},
        {10U, 6U, 4U, 2U, 2U, 6U, 4U, 4U, 0U, 2U},
        {UINT32_MAX, UINT32_MAX - 2U, 2U, 2U, 0U, 5U,
         6U, 1U, 5U, 7U},
        {1U, UINT32_MAX - 2U, 4U, 3U, 1U, 5U, 4U, 4U, 0U, 1U},
        {2U, UINT32_MAX - 5U, 8U, 6U, 2U, 2U, 0U, 0U, 0U, 0U},
        {3U, 3U, 0U, 0U, 0U, 0U, 8U, 5U, 3U, 3U},
    };
    size_t index;

    for (index = 0U; index < sizeof(script) / sizeof(script[0]); ++index) {
        struct dma_ring_view consumer_view;
        struct dma_ring_view producer_view;
        size_t readable;
        size_t writable;

        registers->producer = script[index].producer;
        registers->consumer = script[index].consumer;

        readable = dma_ring_consumer_view(ring, &consumer_view);
        writable = dma_ring_producer_view(ring, &producer_view);

        CHECK(readable == script[index].readable);
        check_view(&consumer_view, storage, script[index].readable,
                   script[index].read_first, script[index].read_second,
                   script[index].read_offset);
        CHECK(writable == script[index].writable);
        check_view(&producer_view, storage, script[index].writable,
                   script[index].write_first, script[index].write_second,
                   script[index].write_offset);
    }
}

static void test_publication_across_wrap(struct dma_ring *ring,
                                         struct dma_ring_registers *registers,
                                         uint8_t *storage)
{
    struct dma_ring_view view;

    registers->producer = UINT32_MAX - 1U;
    registers->consumer = UINT32_MAX - 3U;

    CHECK(!dma_ring_consume(ring, 3U));
    CHECK(registers->consumer == UINT32_MAX - 3U);
    CHECK(dma_ring_consume(ring, 2U));
    CHECK(registers->consumer == UINT32_MAX - 1U);

    CHECK(dma_ring_producer_view(ring, &view) == 8U);
    check_view(&view, storage, 8U, 2U, 6U, 6U);
    view.first.data[0] = 0xa1U;
    view.second.data[0] = 0xb2U;
    CHECK(storage[6] == 0xa1U);
    CHECK(storage[0] == 0xb2U);
    CHECK(registers->producer == UINT32_MAX - 1U);

    CHECK(!dma_ring_produce(ring, 9U));
    CHECK(registers->producer == UINT32_MAX - 1U);
    CHECK(dma_ring_produce(ring, 3U));
    CHECK(registers->producer == 1U);

    CHECK(dma_ring_consumer_view(ring, &view) == 3U);
    check_view(&view, storage, 3U, 2U, 1U, 6U);
    CHECK(!dma_ring_consume(ring, 4U));
    CHECK(registers->consumer == UINT32_MAX - 1U);
    CHECK(dma_ring_consume(ring, 3U));
    CHECK(registers->consumer == 1U);
}

static void test_initialization(void)
{
    struct dma_ring ring;
    struct dma_ring_registers registers = {17U, 9U};
    uint8_t storage[8] = {0U};

    CHECK(!dma_ring_init(NULL, storage, 8U, &registers));
    CHECK(!dma_ring_init(&ring, NULL, 8U, &registers));
    CHECK(!dma_ring_init(&ring, storage, 0U, &registers));
    CHECK(!dma_ring_init(&ring, storage, 6U, &registers));
    CHECK(!dma_ring_init(&ring, storage, 8U, NULL));
    CHECK(dma_ring_init(&ring, storage, 8U, &registers));
    CHECK(registers.producer == 17U);
    CHECK(registers.consumer == 9U);
}

int main(void)
{
    struct dma_ring ring;
    struct dma_ring_registers registers = {0U, 0U};
    uint8_t storage[8] = {0U};

    test_initialization();
    CHECK(dma_ring_init(&ring, storage, 8U, &registers));
    test_scripted_views(&ring, &registers, storage);
    test_publication_across_wrap(&ring, &registers, storage);

    if (failures != 0U) {
        fprintf(stderr, "%u DMA ring checks failed\n", failures);
        return 1;
    }

    puts("DMA ring checks passed");
    return 0;
}
