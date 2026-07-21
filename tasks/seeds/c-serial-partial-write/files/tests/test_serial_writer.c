#include "serial_writer.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ARRAY_LENGTH(array) (sizeof(array) / sizeof((array)[0]))
#define MAX_EVENTS 16U
#define CHECK(expression)          \
    do {                           \
        if (!(expression)) {       \
            return __LINE__;       \
        }                          \
    } while (0)

typedef struct write_event {
    ssize_t result;
    int error_number;
    uint32_t advance_ms;
} write_event_t;

typedef struct wait_event {
    int result;
    int error_number;
    uint32_t advance_ms;
} wait_event_t;

typedef struct fake_serial {
    const uint8_t *expected;
    size_t expected_length;
    uint8_t sent[64];
    size_t sent_length;
    uint64_t clock_ms;
    const write_event_t *writes;
    size_t write_count;
    size_t write_index;
    const wait_event_t *waits;
    size_t wait_count;
    size_t wait_index;
    uint32_t wait_budgets[MAX_EVENTS];
    size_t cancel_after;
    uint64_t cancel_at_ms;
    size_t now_calls;
    size_t cancel_on_now_call;
    int protocol_error;
} fake_serial_t;

static ssize_t fake_write(void *context, const uint8_t *data, size_t length)
{
    fake_serial_t *fake = context;
    const write_event_t *event;

    if (fake->write_index >= fake->write_count) {
        fake->protocol_error = 1;
        errno = EPROTO;
        return -1;
    }

    event = &fake->writes[fake->write_index++];
    fake->clock_ms += event->advance_ms;

    if (fake->sent_length > fake->expected_length ||
        length != fake->expected_length - fake->sent_length ||
        (length != 0U &&
         memcmp(data, fake->expected + fake->sent_length, length) != 0)) {
        fake->protocol_error = 1;
        errno = EPROTO;
        return -1;
    }

    if (event->result > 0) {
        size_t accepted = (size_t)event->result;
        /* Let the transport deliberately over-report so the writer can reject
         * that contract violation with EIO. */
        if (accepted > length) {
            return event->result;
        }
        if (fake->sent_length + accepted > sizeof(fake->sent)) {
            fake->protocol_error = 1;
            errno = EPROTO;
            return -1;
        }
        memcpy(fake->sent + fake->sent_length, data, accepted);
        fake->sent_length += accepted;
    } else if (event->result < 0) {
        errno = event->error_number;
    }

    return event->result;
}

static int fake_wait(void *context, uint32_t timeout_ms)
{
    fake_serial_t *fake = context;
    const wait_event_t *event;

    if (fake->wait_index >= fake->wait_count || fake->wait_index >= MAX_EVENTS) {
        fake->protocol_error = 1;
        errno = EPROTO;
        return -1;
    }

    fake->wait_budgets[fake->wait_index] = timeout_ms;
    event = &fake->waits[fake->wait_index++];
    fake->clock_ms += event->advance_ms;
    if (event->result < 0) {
        errno = event->error_number;
    }
    return event->result;
}

static uint64_t fake_now(void *context)
{
    fake_serial_t *fake = context;
    ++fake->now_calls;
    return fake->clock_ms;
}

static bool fake_cancelled(void *context)
{
    const fake_serial_t *fake = context;
    return fake->sent_length >= fake->cancel_after ||
           fake->clock_ms >= fake->cancel_at_ms ||
           fake->now_calls >= fake->cancel_on_now_call;
}

static fake_serial_t make_fake(const uint8_t *expected,
                               size_t expected_length,
                               const write_event_t *writes,
                               size_t write_count,
                               const wait_event_t *waits,
                               size_t wait_count)
{
    fake_serial_t fake;
    memset(&fake, 0, sizeof(fake));
    fake.expected = expected;
    fake.expected_length = expected_length;
    fake.clock_ms = 1000U;
    fake.writes = writes;
    fake.write_count = write_count;
    fake.waits = waits;
    fake.wait_count = wait_count;
    fake.cancel_after = SIZE_MAX;
    fake.cancel_at_ms = UINT64_MAX;
    fake.cancel_on_now_call = SIZE_MAX;
    return fake;
}

static serial_transport_t make_transport(fake_serial_t *fake)
{
    serial_transport_t transport;
    transport.context = fake;
    transport.write_bytes = fake_write;
    transport.wait_writable = fake_wait;
    transport.now_ms = fake_now;
    transport.is_cancelled = fake_cancelled;
    return transport;
}

static int test_single_write_success(void)
{
    static const uint8_t payload[] = {0x10U, 0x20U, 0x30U, 0x40U};
    static const write_event_t writes[] = {{4, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == 0);
    CHECK(fake.write_index == 1U);
    CHECK(fake.wait_index == 0U);
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(memcmp(fake.sent, payload, sizeof(payload)) == 0);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_optional_cancellation_callback(void)
{
    static const uint8_t payload[] = {0x31U, 0x32U};
    static const write_event_t writes[] = {{2, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);
    transport.is_cancelled = NULL;

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == 0);
    CHECK(fake.write_index == 1U);
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_cancellation_prevents_first_write(void)
{
    static const uint8_t payload[] = {0x41U};
    static const write_event_t writes[] = {{1, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport;
    fake.cancel_after = 0U;
    transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == -1);
    CHECK(errno == ECANCELED);
    CHECK(fake.write_index == 0U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_partial_writes_keep_order(void)
{
    static const uint8_t payload[] = {1U, 2U, 3U, 4U, 5U, 6U};
    static const write_event_t writes[] = {
        {2, 0, 1U}, {1, 0, 1U}, {3, 0, 0U}
    };
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == 0);
    CHECK(fake.write_index == ARRAY_LENGTH(writes));
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(memcmp(fake.sent, payload, sizeof(payload)) == 0);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_interrupted_write_retries(void)
{
    static const uint8_t payload[] = {9U, 8U, 7U, 6U};
    static const write_event_t writes[] = {
        {-1, EINTR, 1U}, {1, 0, 1U}, {-1, EINTR, 1U}, {3, 0, 0U}
    };
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == 0);
    CHECK(fake.write_index == ARRAY_LENGTH(writes));
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(memcmp(fake.sent, payload, sizeof(payload)) == 0);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_waits_receive_remaining_budget(void)
{
    static const uint8_t payload[] = {1U, 3U, 5U, 7U, 9U};
    static const write_event_t writes[] = {
        {-1, EAGAIN, 7U}, {2, 0, 3U}, {-1, EWOULDBLOCK, 2U}, {3, 0, 0U}
    };
    static const wait_event_t waits[] = {
        {1, 0, 4U}, {1, 0, 1U}
    };
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == 0);
    CHECK(fake.wait_index == 2U);
    CHECK(fake.wait_budgets[0] == 13U);
    CHECK(fake.wait_budgets[1] == 4U);
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_zero_progress_waits_then_retries(void)
{
    static const uint8_t payload[] = {0xa1U, 0xb2U};
    static const write_event_t writes[] = {{0, 0, 1U}, {2, 0, 0U}};
    static const wait_event_t waits[] = {{1, 0, 2U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == 0);
    CHECK(fake.write_index == 2U);
    CHECK(fake.wait_index == 1U);
    CHECK(fake.wait_budgets[0] == 9U);
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_infinite_timeout_stays_unbounded(void)
{
    static const uint8_t payload[] = {0xc3U, 0xd4U};
    static const write_event_t writes[] = {
        {-1, EAGAIN, UINT32_MAX}, {2, 0, UINT32_MAX}
    };
    static const wait_event_t waits[] = {{1, 0, UINT32_MAX}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload),
                           SERIAL_TIMEOUT_INFINITE) == 0);
    CHECK(fake.write_index == 2U);
    CHECK(fake.wait_index == 1U);
    CHECK(fake.wait_budgets[0] == SERIAL_TIMEOUT_INFINITE);
    CHECK(fake.sent_length == sizeof(payload));
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_cancellation_is_checked_before_wait(void)
{
    static const uint8_t payload[] = {0xe5U};
    static const write_event_t writes[] = {{-1, EAGAIN, 2U}};
    static const wait_event_t waits[] = {{1, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport;
    fake.cancel_at_ms = 1002U;
    transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == -1);
    CHECK(errno == ECANCELED);
    CHECK(fake.write_index == 1U);
    CHECK(fake.wait_index == 0U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_cancellation_immediately_precedes_wait(void)
{
    static const uint8_t payload[] = {0xe6U};
    static const write_event_t writes[] = {{-1, EAGAIN, 0U}};
    static const wait_event_t waits[] = {{1, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport;
    /* The deadline query immediately before the wait makes cancellation
     * observable; the writer must check again after that query. */
    fake.cancel_on_now_call = 2U;
    transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == -1);
    CHECK(errno == ECANCELED);
    CHECK(fake.write_index == 1U);
    CHECK(fake.wait_index == 0U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_timeout_budget_is_not_restarted(void)
{
    static const uint8_t payload[] = {0xaaU};
    static const write_event_t writes[] = {
        {-1, EAGAIN, 4U}, {-1, EAGAIN, 2U}
    };
    static const wait_event_t waits[] = {
        {1, 0, 3U}, {0, 0, 1U}
    };
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == -1);
    CHECK(errno == ETIMEDOUT);
    CHECK(fake.wait_index == 2U);
    CHECK(fake.wait_budgets[0] == 6U);
    CHECK(fake.wait_budgets[1] == 1U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_zero_timeout_allows_only_immediate_attempt(void)
{
    static const uint8_t payload[] = {0x01U, 0x02U};
    static const write_event_t writes[] = {{1, 0, 0U}, {1, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 0U) == -1);
    CHECK(errno == ETIMEDOUT);
    CHECK(fake.write_index == 1U);
    CHECK(fake.sent_length == 1U);
    CHECK(fake.wait_index == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_partial_write_cannot_cross_deadline(void)
{
    static const uint8_t payload[] = {0x11U, 0x22U, 0x33U};
    static const write_event_t writes[] = {{1, 0, 5U}, {2, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 5U) == -1);
    CHECK(errno == ETIMEDOUT);
    CHECK(fake.write_index == 1U);
    CHECK(fake.sent_length == 1U);
    CHECK(fake.sent[0] == payload[0]);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_readiness_cannot_cross_deadline(void)
{
    static const uint8_t payload[] = {0x44U};
    static const write_event_t writes[] = {
        {-1, EAGAIN, 0U}, {1, 0, 0U}
    };
    static const wait_event_t waits[] = {{1, 0, 5U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 5U) == -1);
    CHECK(errno == ETIMEDOUT);
    CHECK(fake.write_index == 1U);
    CHECK(fake.wait_index == 1U);
    CHECK(fake.wait_budgets[0] == 5U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_eintr_cannot_restart_expired_budget(void)
{
    static const uint8_t payload[] = {0x55U};
    static const write_event_t writes[] = {{-1, EINTR, 5U}, {1, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 5U) == -1);
    CHECK(errno == ETIMEDOUT);
    CHECK(fake.write_index == 1U);
    CHECK(fake.sent_length == 0U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_cancellation_stops_after_partial_progress(void)
{
    static const uint8_t payload[] = {2U, 4U, 6U, 8U};
    static const write_event_t writes[] = {{2, 0, 1U}, {2, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport;
    fake.cancel_after = 2U;
    transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == -1);
    CHECK(errno == ECANCELED);
    CHECK(fake.write_index == 1U);
    CHECK(fake.sent_length == 2U);
    CHECK(memcmp(fake.sent, payload, fake.sent_length) == 0);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_write_error_keeps_original_cause(void)
{
    static const uint8_t payload[] = {11U, 12U, 13U, 14U};
    static const write_event_t writes[] = {{2, 0, 1U}, {-1, EPIPE, 1U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == -1);
    CHECK(errno == EPIPE);
    CHECK(fake.write_index == 2U);
    CHECK(fake.sent_length == 2U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_wait_error_keeps_original_cause(void)
{
    static const uint8_t payload[] = {0xfeU};
    static const write_event_t writes[] = {{-1, EAGAIN, 1U}};
    static const wait_event_t waits[] = {{-1, ENODEV, 1U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), waits,
                                   ARRAY_LENGTH(waits));
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 20U) == -1);
    CHECK(errno == ENODEV);
    CHECK(fake.write_index == 1U);
    CHECK(fake.wait_index == 1U);
    CHECK(fake.wait_budgets[0] == 19U);
    CHECK(fake.protocol_error == 0);
    return 0;
}

static int test_impossible_write_count_is_rejected(void)
{
    static const uint8_t payload[] = {0x61U, 0x62U, 0x63U};
    static const write_event_t writes[] = {{1, 0, 0U}, {3, 0, 0U}};
    fake_serial_t fake = make_fake(payload, sizeof(payload), writes,
                                   ARRAY_LENGTH(writes), NULL, 0U);
    serial_transport_t transport = make_transport(&fake);

    errno = 0;
    CHECK(serial_write_all(&transport, payload, sizeof(payload), 10U) == -1);
    CHECK(errno == EIO);
    CHECK(fake.write_index == 2U);
    CHECK(fake.sent_length == 1U);
    CHECK(fake.sent[0] == payload[0]);
    CHECK(fake.protocol_error == 0);
    return 0;
}

typedef int (*test_function_t)(void);

typedef struct test_case {
    const char *name;
    test_function_t function;
} test_case_t;

int main(void)
{
    static const test_case_t tests[] = {
        {"single write success", test_single_write_success},
        {"optional cancellation callback", test_optional_cancellation_callback},
        {"cancellation before first write", test_cancellation_prevents_first_write},
        {"partial writes keep order", test_partial_writes_keep_order},
        {"interrupted write retries", test_interrupted_write_retries},
        {"waits receive remaining budget", test_waits_receive_remaining_budget},
        {"zero progress waits then retries", test_zero_progress_waits_then_retries},
        {"infinite timeout stays unbounded", test_infinite_timeout_stays_unbounded},
        {"cancellation before wait", test_cancellation_is_checked_before_wait},
        {"cancellation immediately before wait", test_cancellation_immediately_precedes_wait},
        {"timeout budget is not restarted", test_timeout_budget_is_not_restarted},
        {"zero timeout immediate attempt", test_zero_timeout_allows_only_immediate_attempt},
        {"partial write respects deadline", test_partial_write_cannot_cross_deadline},
        {"readiness respects deadline", test_readiness_cannot_cross_deadline},
        {"EINTR respects expired budget", test_eintr_cannot_restart_expired_budget},
        {"cancellation after partial progress", test_cancellation_stops_after_partial_progress},
        {"write error causality", test_write_error_keeps_original_cause},
        {"wait error causality", test_wait_error_keeps_original_cause},
        {"impossible write count", test_impossible_write_count_is_rejected}
    };
    size_t index;
    int failures = 0;

    for (index = 0U; index < ARRAY_LENGTH(tests); ++index) {
        int line = tests[index].function();
        if (line == 0) {
            printf("PASS: %s\n", tests[index].name);
        } else {
            fprintf(stderr, "FAIL: %s (line %d)\n", tests[index].name, line);
            ++failures;
        }
    }

    if (failures != 0) {
        fprintf(stderr, "%d test(s) failed\n", failures);
        return 1;
    }
    return 0;
}
