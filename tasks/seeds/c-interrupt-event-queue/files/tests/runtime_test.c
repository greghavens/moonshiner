#include "event_queue.h"

#include <stdbool.h>
#include <stddef.h>
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

struct event_log {
    struct event_queue_event events[16];
    size_t length;
};

struct handler_context {
    struct event_queue *queue;
    struct event_log *log;
    struct event_queue_event interrupt_event;
    bool inject_interrupt;
    bool interrupt_accepted;
};

static struct event_queue_event make_event(uint8_t source, uint16_t value)
{
    struct event_queue_event event;

    event.source = source;
    event.value = value;
    return event;
}

static void collect_event(const struct event_queue_event *event, void *opaque)
{
    struct handler_context *context = opaque;

    if (context->log->length <
        sizeof(context->log->events) / sizeof(context->log->events[0])) {
        context->log->events[context->log->length] = *event;
        ++context->log->length;
    }

    if (context->inject_interrupt) {
        context->inject_interrupt = false;
        context->interrupt_accepted =
            event_queue_push_from_isr(context->queue,
                                      context->interrupt_event);
    }
}

static void check_event(const struct event_log *log,
                        size_t index,
                        uint8_t source,
                        uint16_t value)
{
    CHECK(index < log->length);
    if (index < log->length) {
        CHECK(log->events[index].source == source);
        CHECK(log->events[index].value == value);
    }
}

static struct handler_context make_context(struct event_queue *queue,
                                           struct event_log *log)
{
    struct handler_context context;

    context.queue = queue;
    context.log = log;
    context.interrupt_event = make_event(0U, 0U);
    context.inject_interrupt = false;
    context.interrupt_accepted = false;
    return context;
}

static void test_fifo_and_physical_wrap(void)
{
    struct event_queue queue;
    struct event_log log = {{{0U, 0U}}, 0U};
    struct handler_context context;

    event_queue_init(&queue);
    context = make_context(&queue, &log);

    CHECK(event_queue_push_from_isr(&queue, make_event(1U, 101U)));
    CHECK(event_queue_push_from_isr(&queue, make_event(2U, 202U)));
    CHECK(event_queue_push_from_isr(&queue, make_event(3U, 303U)));
    CHECK(event_queue_pending(&queue) == 3U);

    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_push_from_isr(&queue, make_event(4U, 404U)));
    CHECK(event_queue_push_from_isr(&queue, make_event(5U, 505U)));
    CHECK(event_queue_pending(&queue) == 3U);

    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(!event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_pending(&queue) == 0U);
    CHECK(event_queue_dropped(&queue) == 0U);

    CHECK(log.length == 5U);
    check_event(&log, 0U, 1U, 101U);
    check_event(&log, 1U, 2U, 202U);
    check_event(&log, 2U, 3U, 303U);
    check_event(&log, 3U, 4U, 404U);
    check_event(&log, 4U, 5U, 505U);
}

static void test_interrupt_during_dispatch(void)
{
    struct event_queue queue;
    struct event_log log = {{{0U, 0U}}, 0U};
    struct handler_context context;

    event_queue_init(&queue);
    context = make_context(&queue, &log);

    CHECK(event_queue_push_from_isr(&queue, make_event(7U, 700U)));
    context.interrupt_event = make_event(8U, 800U);
    context.inject_interrupt = true;

    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(context.interrupt_accepted);
    CHECK(event_queue_pending(&queue) == 1U);
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(event_queue_pending(&queue) == 0U);
    CHECK(event_queue_dropped(&queue) == 0U);

    CHECK(log.length == 2U);
    check_event(&log, 0U, 7U, 700U);
    check_event(&log, 1U, 8U, 800U);
}

static void test_drop_newest_overflow(void)
{
    struct event_queue queue;
    struct event_log log = {{{0U, 0U}}, 0U};
    struct handler_context context;
    unsigned int index;

    event_queue_init(&queue);
    context = make_context(&queue, &log);

    for (index = 0U; index < EVENT_QUEUE_CAPACITY; ++index) {
        CHECK(event_queue_push_from_isr(
            &queue, make_event((uint8_t)(10U + index),
                               (uint16_t)(1000U + index))));
    }
    CHECK(event_queue_pending(&queue) == EVENT_QUEUE_CAPACITY);
    CHECK(!event_queue_push_from_isr(&queue, make_event(99U, 9999U)));
    CHECK(event_queue_pending(&queue) == EVENT_QUEUE_CAPACITY);
    CHECK(event_queue_dropped(&queue) == 1U);

    for (index = 0U; index < EVENT_QUEUE_CAPACITY; ++index) {
        CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    }
    CHECK(log.length == EVENT_QUEUE_CAPACITY);
    for (index = 0U; index < EVENT_QUEUE_CAPACITY; ++index) {
        check_event(&log, index, (uint8_t)(10U + index),
                    (uint16_t)(1000U + index));
    }

    CHECK(event_queue_push_from_isr(&queue, make_event(20U, 2000U)));
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    check_event(&log, EVENT_QUEUE_CAPACITY, 20U, 2000U);
}

static void test_full_queue_stays_reserved_during_handler(void)
{
    struct event_queue queue;
    struct event_log log = {{{0U, 0U}}, 0U};
    struct handler_context context;
    unsigned int index;

    event_queue_init(&queue);
    context = make_context(&queue, &log);

    for (index = 0U; index < EVENT_QUEUE_CAPACITY; ++index) {
        CHECK(event_queue_push_from_isr(
            &queue, make_event((uint8_t)(30U + index),
                               (uint16_t)(3000U + index))));
    }

    context.interrupt_event = make_event(40U, 4000U);
    context.inject_interrupt = true;
    CHECK(event_queue_dispatch_one(&queue, collect_event, &context));
    CHECK(!context.interrupt_accepted);
    CHECK(event_queue_pending(&queue) == EVENT_QUEUE_CAPACITY - 1U);
    CHECK(event_queue_dropped(&queue) == 1U);

    while (event_queue_dispatch_one(&queue, collect_event, &context)) {
    }
    CHECK(log.length == EVENT_QUEUE_CAPACITY);
    for (index = 0U; index < EVENT_QUEUE_CAPACITY; ++index) {
        check_event(&log, index, (uint8_t)(30U + index),
                    (uint16_t)(3000U + index));
    }
}

static void test_null_contract(void)
{
    struct event_queue queue;
    struct event_log log = {{{0U, 0U}}, 0U};
    struct handler_context context;

    event_queue_init(NULL);
    event_queue_init(&queue);
    context = make_context(&queue, &log);

    CHECK(!event_queue_push_from_isr(NULL, make_event(1U, 1U)));
    CHECK(!event_queue_dispatch_one(NULL, collect_event, &context));
    CHECK(!event_queue_dispatch_one(&queue, NULL, &context));
    CHECK(event_queue_pending(NULL) == 0U);
    CHECK(event_queue_dropped(NULL) == 0U);
}

int main(void)
{
    test_fifo_and_physical_wrap();
    test_interrupt_during_dispatch();
    test_drop_newest_overflow();
    test_full_queue_stays_reserved_during_handler();
    test_null_contract();

    if (failures != 0U) {
        fprintf(stderr, "%u event queue checks failed\n", failures);
        return 1;
    }

    puts("event queue checks passed");
    return 0;
}
