#include "event_queue.h"

#include <stddef.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
               "event queue requires always-lock-free unsigned int atomics");

static unsigned int advance_index(unsigned int index)
{
    return (index + 1U) % EVENT_QUEUE_CAPACITY;
}

void event_queue_init(struct event_queue *queue)
{
    if (queue == NULL) {
        return;
    }

    queue->write_index = 0U;
    queue->read_index = 0U;
    atomic_init(&queue->count, 0U);
    atomic_init(&queue->dropped, 0U);
}

bool event_queue_push_from_isr(struct event_queue *queue,
                               struct event_queue_event event)
{
    unsigned int used;

    if (queue == NULL) {
        return false;
    }

    used = atomic_load_explicit(&queue->count, memory_order_acquire);
    if (used == EVENT_QUEUE_CAPACITY) {
        (void)atomic_fetch_add_explicit(&queue->dropped, 1U,
                                        memory_order_relaxed);
        return false;
    }

    queue->slots[queue->write_index] = event;
    queue->write_index = advance_index(queue->write_index);
    (void)atomic_fetch_add_explicit(&queue->count, 1U,
                                    memory_order_release);
    return true;
}

bool event_queue_dispatch_one(struct event_queue *queue,
                              event_queue_handler handler,
                              void *context)
{
    struct event_queue_event event;
    unsigned int available;

    if (queue == NULL || handler == NULL) {
        return false;
    }

    available = atomic_load_explicit(&queue->count, memory_order_acquire);
    if (available == 0U) {
        return false;
    }

    event = queue->slots[queue->read_index];
    queue->read_index = advance_index(queue->read_index);
    handler(&event, context);

    atomic_store_explicit(&queue->count, available - 1U,
                          memory_order_release);
    return true;
}

unsigned int event_queue_pending(const struct event_queue *queue)
{
    if (queue == NULL) {
        return 0U;
    }

    return atomic_load_explicit(&queue->count, memory_order_acquire);
}

unsigned int event_queue_dropped(const struct event_queue *queue)
{
    if (queue == NULL) {
        return 0U;
    }

    return atomic_load_explicit(&queue->dropped, memory_order_relaxed);
}
