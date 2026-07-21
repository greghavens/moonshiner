#ifndef EVENT_QUEUE_H
#define EVENT_QUEUE_H

#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>

#define EVENT_QUEUE_CAPACITY 4U

struct event_queue_event {
    uint8_t source;
    uint16_t value;
};

typedef void (*event_queue_handler)(const struct event_queue_event *event,
                                    void *context);

struct event_queue {
    struct event_queue_event slots[EVENT_QUEUE_CAPACITY];
    unsigned int write_index;
    unsigned int read_index;
    _Atomic unsigned int count;
    _Atomic unsigned int dropped;
};

void event_queue_init(struct event_queue *queue);

bool event_queue_push_from_isr(struct event_queue *queue,
                               struct event_queue_event event);
bool event_queue_dispatch_one(struct event_queue *queue,
                              event_queue_handler handler,
                              void *context);

unsigned int event_queue_pending(const struct event_queue *queue);
unsigned int event_queue_dropped(const struct event_queue *queue);

#endif
