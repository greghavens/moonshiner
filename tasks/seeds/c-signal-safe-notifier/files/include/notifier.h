#ifndef NOTIFIER_H
#define NOTIFIER_H

#include <signal.h>
#include <stdbool.h>
#include <stdatomic.h>

typedef void (*notifier_callback)(void *context);

enum notifier_wait_result {
    NOTIFIER_ERROR = -1,
    NOTIFIER_TIMEOUT = 0,
    NOTIFIER_DISPATCHED = 1,
    NOTIFIER_STOPPED = 2
};

/*
 * This small notifier deliberately supports one active instance at a time.
 * Signals which arrive before a call to notifier_wait() drains the notifier
 * are coalesced into one callback on the thread calling notifier_wait().
 */
typedef struct notifier {
    int read_fd;
    int write_fd;
    int signum;
    notifier_callback callback;
    void *context;
    atomic_bool stopping;
    struct sigaction previous_action;
    bool previous_action_valid;
    bool initialized;
} notifier;

int notifier_init(notifier *instance, int signum,
                  notifier_callback callback, void *context);
int notifier_wait(notifier *instance, int timeout_ms);
int notifier_shutdown(notifier *instance);
void notifier_destroy(notifier *instance);

#endif
