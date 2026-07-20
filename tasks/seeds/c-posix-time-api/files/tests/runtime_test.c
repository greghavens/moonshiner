#include "event_time.h"

#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct sample {
    time_t instant;
    const char *expected;
};

static const struct sample samples[] = {
    {(time_t)-1, "1969-12-31T23:59:59Z"},
    {(time_t)0, "1970-01-01T00:00:00Z"},
    {(time_t)951827696, "2000-02-29T12:34:56Z"},
    {(time_t)4107544496LL, "2100-03-01T00:34:56Z"},
};

struct worker {
    size_t offset;
    atomic_int *failed;
};

static int check(int condition, const char *message)
{
    if (!condition) {
        (void)fprintf(stderr, "FAIL: %s\n", message);
        return 0;
    }
    return 1;
}

static void *format_worker(void *opaque)
{
    struct worker *worker = opaque;

    for (size_t iteration = 0U; iteration < 20000U; ++iteration) {
        const struct sample *sample =
            &samples[(iteration + worker->offset) % (sizeof(samples) / sizeof(samples[0]))];
        char output[EVENT_TIME_UTC_SIZE];

        if (event_time_format_utc(sample->instant, output, sizeof(output)) != 0 ||
            strcmp(output, sample->expected) != 0) {
            atomic_store(worker->failed, 1);
            break;
        }
    }

    return NULL;
}

static int test_exact_output(void)
{
    for (size_t index = 0U; index < sizeof(samples) / sizeof(samples[0]); ++index) {
        struct {
            char output[EVENT_TIME_UTC_SIZE];
            char guard;
        } storage;

        (void)memset(&storage, 'X', sizeof(storage));
        if (!check(event_time_format_utc(samples[index].instant, storage.output,
                                         sizeof(storage.output)) == 0,
                   "valid timestamp must succeed") ||
            !check(strcmp(storage.output, samples[index].expected) == 0,
                   "timestamp bytes changed") ||
            !check(storage.guard == 'X', "formatter wrote past the documented buffer")) {
            return 0;
        }
    }
    return 1;
}

static int test_errors(void)
{
    char output[EVENT_TIME_UTC_SIZE];
    char untouched = 'X';

    if (!check(event_time_format_utc((time_t)0, NULL, EVENT_TIME_UTC_SIZE) == EINVAL,
               "null destination must return EINVAL") ||
        !check(event_time_format_utc((time_t)0, &untouched, 0U) == EINVAL,
               "zero capacity must return EINVAL") ||
        !check(untouched == 'X', "zero-capacity destination must not be accessed")) {
        return 0;
    }

    (void)memset(output, 'X', sizeof(output));
    if (!check(event_time_format_utc((time_t)0, output, EVENT_TIME_UTC_SIZE - 1U) == ERANGE,
               "short destination must return ERANGE") ||
        !check(output[0] == '\0', "short destination must be emptied")) {
        return 0;
    }

    (void)memset(output, 'X', sizeof(output));
    if (!check(sizeof(time_t) >= sizeof(int64_t), "tests require a 64-bit time_t") ||
        !check(event_time_format_utc((time_t)INT64_MAX, output, sizeof(output)) == EOVERFLOW,
               "unrepresentable timestamp must return EOVERFLOW") ||
        !check(output[0] == '\0', "conversion failure must empty the destination")) {
        return 0;
    }

    return 1;
}

static int test_concurrent_calls(void)
{
    enum { thread_count = 8 };
    pthread_t threads[thread_count];
    struct worker workers[thread_count];
    atomic_int failed;

    atomic_init(&failed, 0);
    for (size_t index = 0U; index < thread_count; ++index) {
        workers[index].offset = index;
        workers[index].failed = &failed;
        if (!check(pthread_create(&threads[index], NULL, format_worker, &workers[index]) == 0,
                   "pthread_create failed")) {
            return 0;
        }
    }

    for (size_t index = 0U; index < thread_count; ++index) {
        if (!check(pthread_join(threads[index], NULL) == 0, "pthread_join failed")) {
            return 0;
        }
    }

    return check(atomic_load(&failed) == 0, "concurrent formatting corrupted output");
}

int main(void)
{
    if (!test_exact_output() || !test_errors() || !test_concurrent_calls()) {
        return 1;
    }

    (void)puts("all runtime checks passed");
    return 0;
}
