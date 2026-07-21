#define _POSIX_C_SOURCE 200809L

#include "notifier.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct callback_state {
    atomic_int calls;
    pthread_t thread;
} callback_state;

static void fail_at(const char *file, int line, const char *message)
{
    fprintf(stderr, "%s:%d: %s\n", file, line, message);
    exit(EXIT_FAILURE);
}

#define CHECK(condition, message) \
    do { if (!(condition)) fail_at(__FILE__, __LINE__, (message)); } while (0)

int __real_poll(struct pollfd *fds, nfds_t count, int timeout);
int __real_clock_gettime(clockid_t clock_id, struct timespec *time_value);

static int fake_deadline_clock;
static int fake_clock_calls;
static int fake_poll_calls;
static int fake_poll_timeouts[2];

static pthread_mutex_t poll_observer_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t poll_observer_condition = PTHREAD_COND_INITIALIZER;
static int observe_blocking_poll;
static int blocking_poll_entered;

int __wrap_clock_gettime(clockid_t clock_id, struct timespec *time_value)
{
    if (fake_deadline_clock && clock_id == CLOCK_MONOTONIC) {
        if (fake_clock_calls == 0) {
            time_value->tv_sec = 100;
            time_value->tv_nsec = 900500000L;
        } else if (fake_clock_calls == 1) {
            time_value->tv_sec = 100;
            time_value->tv_nsec = 951100000L;
        } else {
            errno = EOVERFLOW;
            return -1;
        }
        ++fake_clock_calls;
        return 0;
    }
    return __real_clock_gettime(clock_id, time_value);
}

int __wrap_poll(struct pollfd *fds, nfds_t count, int timeout)
{
    if (fake_deadline_clock) {
        if (fake_poll_calls < 2) {
            fake_poll_timeouts[fake_poll_calls] = timeout;
        }
        ++fake_poll_calls;
        if (fake_poll_calls == 1) {
            errno = EINTR;
            return -1;
        }
        if (fake_poll_calls == 2) {
            return 0;
        }
        errno = EIO;
        return -1;
    }

    CHECK(pthread_mutex_lock(&poll_observer_mutex) == 0,
          "could not lock poll observer");
    if (observe_blocking_poll && timeout == -1) {
        blocking_poll_entered = 1;
        CHECK(pthread_cond_broadcast(&poll_observer_condition) == 0,
              "could not notify poll observer");
    }
    CHECK(pthread_mutex_unlock(&poll_observer_mutex) == 0,
          "could not unlock poll observer");
    return __real_poll(fds, count, timeout);
}

static void record_callback(void *context)
{
    callback_state *state = context;

    /* User callbacks may perform work which is not async-signal-safe. */
    state->thread = pthread_self();
    atomic_fetch_add_explicit(&state->calls, 1, memory_order_relaxed);
}

typedef struct raise_request {
    int signum;
    int count;
} raise_request;

static void *raise_signals(void *context)
{
    raise_request *request = context;
    int index;

    for (index = 0; index < request->count; ++index) {
        if (raise(request->signum) != 0) {
            return (void *)1;
        }
    }
    return NULL;
}

static void test_callback_is_deferred_and_coalesced(void)
{
    notifier instance;
    callback_state state = { .calls = ATOMIC_VAR_INIT(0) };
    raise_request request = { .signum = SIGUSR1, .count = 64 };
    pthread_t producer;
    void *thread_result;

    CHECK(notifier_init(&instance, SIGUSR1, record_callback, &state) == 0,
          "notifier_init failed");
    CHECK(pthread_create(&producer, NULL, raise_signals, &request) == 0,
          "could not create signal producer");
    CHECK(pthread_join(producer, &thread_result) == 0 && thread_result == NULL,
          "signal producer failed");
    CHECK(atomic_load_explicit(&state.calls, memory_order_relaxed) == 0,
          "callback ran inside the signal handler");

    CHECK(notifier_wait(&instance, 500) == NOTIFIER_DISPATCHED,
          "queued signal was not dispatched");
    CHECK(atomic_load_explicit(&state.calls, memory_order_relaxed) == 1,
          "pending signals were not coalesced");
    CHECK(pthread_equal(state.thread, pthread_self()),
          "callback did not run on the waiting thread");
    CHECK(notifier_wait(&instance, 0) == NOTIFIER_TIMEOUT,
          "coalesced signals left extra callbacks queued");

    CHECK(notifier_shutdown(&instance) == 0, "notifier_shutdown failed");
    notifier_destroy(&instance);
}

static void test_handler_preserves_errno(void)
{
    notifier instance;
    callback_state state = { .calls = ATOMIC_VAR_INIT(0) };
    unsigned char fill[4096];
    unsigned char drain[4096];
    int flags;

    CHECK(notifier_init(&instance, SIGUSR1, record_callback, &state) == 0,
          "notifier_init failed");
    flags = fcntl(instance.write_fd, F_GETFL);
    CHECK(flags != -1 && (flags & O_NONBLOCK) != 0,
          "handler notification descriptor is not nonblocking");

    memset(fill, 'F', sizeof(fill));
    for (;;) {
        ssize_t count = write(instance.write_fd, fill, sizeof(fill));

        if (count > 0 || (count == -1 && errno == EINTR)) {
            continue;
        }
        CHECK(count == -1 && (errno == EAGAIN || errno == EWOULDBLOCK),
              "could not saturate the notification path");
        break;
    }
    for (;;) {
        ssize_t count = write(instance.write_fd, fill, 1);

        if (count == 1 || (count == -1 && errno == EINTR)) {
            continue;
        }
        CHECK(count == -1 && (errno == EAGAIN || errno == EWOULDBLOCK),
              "could not completely fill the notification path");
        break;
    }

    errno = EDOM;
    CHECK(raise(SIGUSR1) == 0, "raise failed");
    CHECK(errno == EDOM, "signal handler did not preserve errno");
    CHECK(atomic_load_explicit(&state.calls, memory_order_relaxed) == 0,
          "errno test callback was not deferred");

    for (;;) {
        ssize_t count = read(instance.read_fd, drain, sizeof(drain));

        if (count > 0 || (count == -1 && errno == EINTR)) {
            continue;
        }
        CHECK(count == -1 && (errno == EAGAIN || errno == EWOULDBLOCK),
              "could not drain the saturated notification path");
        break;
    }

    CHECK(notifier_shutdown(&instance) == 0, "notifier_shutdown failed");
    notifier_destroy(&instance);
}

static void test_eintr_does_not_end_or_extend_wait(void)
{
    notifier instance;
    callback_state state = { .calls = ATOMIC_VAR_INIT(0) };

    CHECK(notifier_init(&instance, SIGUSR1, record_callback, &state) == 0,
          "notifier_init failed");

    fake_clock_calls = 0;
    fake_poll_calls = 0;
    fake_deadline_clock = 1;
    CHECK(notifier_wait(&instance, 120) == NOTIFIER_TIMEOUT,
          "EINTR ended a finite wait early");
    fake_deadline_clock = 0;
    CHECK(fake_clock_calls == 2, "finite wait did not retain one deadline");
    CHECK(fake_poll_calls == 2, "finite wait did not retry once after EINTR");
    CHECK(fake_poll_timeouts[0] == 120,
          "finite wait did not begin with the requested timeout");
    CHECK(fake_poll_timeouts[1] == 70,
          "finite wait restarted or rounded past its deadline after EINTR");

    CHECK(notifier_shutdown(&instance) == 0, "notifier_shutdown failed");
    notifier_destroy(&instance);
}

typedef struct wait_request {
    notifier *instance;
    atomic_int entered;
    int result;
} wait_request;

static void *blocking_wait(void *context)
{
    wait_request *request = context;

    atomic_store_explicit(&request->entered, 1, memory_order_release);
    request->result = notifier_wait(request->instance, -1);
    return NULL;
}

static void test_shutdown_wakes_waiter_without_callback(void)
{
    notifier instance;
    callback_state state = { .calls = ATOMIC_VAR_INIT(0) };
    wait_request request = {
        .instance = &instance,
        .entered = ATOMIC_VAR_INIT(0),
        .result = NOTIFIER_ERROR
    };
    pthread_t waiter;

    CHECK(notifier_init(&instance, SIGUSR1, record_callback, &state) == 0,
          "notifier_init failed");
    CHECK(pthread_mutex_lock(&poll_observer_mutex) == 0,
          "could not lock poll observer");
    blocking_poll_entered = 0;
    observe_blocking_poll = 1;
    CHECK(pthread_mutex_unlock(&poll_observer_mutex) == 0,
          "could not unlock poll observer");
    CHECK(pthread_create(&waiter, NULL, blocking_wait, &request) == 0,
          "could not create waiter");

    CHECK(pthread_mutex_lock(&poll_observer_mutex) == 0,
          "could not lock poll observer");
    while (!blocking_poll_entered) {
        CHECK(pthread_cond_wait(&poll_observer_condition,
                                &poll_observer_mutex) == 0,
              "could not wait for blocking poll");
    }
    CHECK(pthread_mutex_unlock(&poll_observer_mutex) == 0,
          "could not unlock poll observer");

    CHECK(notifier_shutdown(&instance) == 0, "notifier_shutdown failed");
    CHECK(pthread_join(waiter, NULL) == 0, "could not join waiter");
    CHECK(pthread_mutex_lock(&poll_observer_mutex) == 0,
          "could not lock poll observer");
    observe_blocking_poll = 0;
    CHECK(pthread_mutex_unlock(&poll_observer_mutex) == 0,
          "could not unlock poll observer");
    CHECK(request.result == NOTIFIER_STOPPED,
          "shutdown did not wake the blocked waiter");
    CHECK(atomic_load_explicit(&state.calls, memory_order_relaxed) == 0,
          "shutdown delivered a callback");
    notifier_destroy(&instance);
}

static void test_only_one_notifier_is_active(void)
{
    notifier first;
    notifier second;
    callback_state state = { .calls = ATOMIC_VAR_INIT(0) };

    CHECK(notifier_init(&first, SIGUSR1, record_callback, &state) == 0,
          "first notifier_init failed");
    errno = 0;
    CHECK(notifier_init(&second, SIGUSR1, record_callback, &state) == -1 &&
              errno == EBUSY,
          "a second active notifier was accepted");
    CHECK(notifier_shutdown(&first) == 0, "first notifier_shutdown failed");
    notifier_destroy(&first);

    CHECK(notifier_init(&second, SIGUSR1, record_callback, &state) == 0,
          "notifier slot was not released by shutdown");
    CHECK(notifier_shutdown(&second) == 0,
          "second notifier_shutdown failed");
    notifier_destroy(&second);
}

int main(void)
{
    sigset_t unblocked;
    sigset_t previous_mask;

    sigemptyset(&unblocked);
    sigaddset(&unblocked, SIGUSR1);
    CHECK(pthread_sigmask(SIG_UNBLOCK, &unblocked, &previous_mask) == 0,
          "could not unblock the notifier test signal");

    test_callback_is_deferred_and_coalesced();
    test_handler_preserves_errno();
    test_eintr_does_not_end_or_extend_wait();
    test_shutdown_wakes_waiter_without_callback();
    test_only_one_notifier_is_active();

    CHECK(pthread_sigmask(SIG_SETMASK, &previous_mask, NULL) == 0,
          "could not restore the original signal mask");
    puts("all notifier tests passed");
    return EXIT_SUCCESS;
}
