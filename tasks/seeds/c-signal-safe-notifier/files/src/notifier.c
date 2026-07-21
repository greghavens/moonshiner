#define _POSIX_C_SOURCE 200809L

#include "notifier.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static notifier *active_notifier;

static void notifier_signal_handler(int signum)
{
    int saved_errno = errno;
    notifier *instance = active_notifier;

    (void)signum;
    if (instance != NULL && instance->callback != NULL) {
        /* BUG: arbitrary user work is not safe in a signal handler. */
        instance->callback(instance->context);
    }
    errno = saved_errno;
}

static int configure_pipe_end(int fd, int command, int flag)
{
    int current = fcntl(fd, command);

    if (current == -1 || fcntl(fd, command == F_GETFL ? F_SETFL : F_SETFD,
                                current | flag) == -1) {
        return -1;
    }
    return 0;
}

static int make_pipe(int descriptors[2])
{
    if (pipe(descriptors) == -1) {
        return -1;
    }
    if (configure_pipe_end(descriptors[0], F_GETFL, O_NONBLOCK) == -1 ||
        configure_pipe_end(descriptors[1], F_GETFL, O_NONBLOCK) == -1 ||
        configure_pipe_end(descriptors[0], F_GETFD, FD_CLOEXEC) == -1 ||
        configure_pipe_end(descriptors[1], F_GETFD, FD_CLOEXEC) == -1) {
        int saved_errno = errno;
        close(descriptors[0]);
        close(descriptors[1]);
        errno = saved_errno;
        return -1;
    }
    return 0;
}

int notifier_init(notifier *instance, int signum,
                  notifier_callback callback, void *context)
{
    int descriptors[2];
    sigset_t blocked;
    sigset_t previous_mask;
    struct sigaction action;

    if (instance == NULL || callback == NULL || signum <= 0) {
        errno = EINVAL;
        return -1;
    }
    if (active_notifier != NULL) {
        errno = EBUSY;
        return -1;
    }
    if (make_pipe(descriptors) == -1) {
        return -1;
    }

    memset(instance, 0, sizeof(*instance));
    instance->read_fd = descriptors[0];
    instance->write_fd = descriptors[1];
    instance->signum = signum;
    instance->callback = callback;
    instance->context = context;
    atomic_init(&instance->stopping, false);

    sigemptyset(&blocked);
    sigaddset(&blocked, signum);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous_mask) == -1) {
        goto fail_pipe;
    }

    memset(&action, 0, sizeof(action));
    action.sa_handler = notifier_signal_handler;
    sigemptyset(&action.sa_mask);
    action.sa_flags = 0;
    active_notifier = instance;
    if (sigaction(signum, &action, &instance->previous_action) == -1) {
        active_notifier = NULL;
        sigprocmask(SIG_SETMASK, &previous_mask, NULL);
        goto fail_pipe;
    }
    instance->previous_action_valid = true;
    instance->initialized = true;
    if (sigprocmask(SIG_SETMASK, &previous_mask, NULL) == -1) {
        int saved_errno = errno;
        notifier_shutdown(instance);
        notifier_destroy(instance);
        errno = saved_errno;
        return -1;
    }
    return 0;

fail_pipe:
    {
        int saved_errno = errno;
        close(descriptors[0]);
        close(descriptors[1]);
        errno = saved_errno;
    }
    return -1;
}

static int remaining_timeout_ms(const struct timespec *deadline)
{
    struct timespec now;
    int64_t milliseconds;

    if (clock_gettime(CLOCK_MONOTONIC, &now) == -1) {
        return -1;
    }
    milliseconds = (int64_t)(deadline->tv_sec - now.tv_sec) * 1000;
    milliseconds += (deadline->tv_nsec - now.tv_nsec + 999999) / 1000000;
    if (milliseconds <= 0) {
        return 0;
    }
    if (milliseconds > INT32_MAX) {
        return INT32_MAX;
    }
    return (int)milliseconds;
}

int notifier_wait(notifier *instance, int timeout_ms)
{
    struct pollfd poll_fd;
    struct timespec deadline;
    int current_timeout = timeout_ms;

    if (instance == NULL || !instance->initialized || timeout_ms < -1) {
        errno = EINVAL;
        return NOTIFIER_ERROR;
    }
    if (atomic_load_explicit(&instance->stopping, memory_order_acquire)) {
        return NOTIFIER_STOPPED;
    }
    if (timeout_ms >= 0) {
        if (clock_gettime(CLOCK_MONOTONIC, &deadline) == -1) {
            return NOTIFIER_ERROR;
        }
        deadline.tv_sec += timeout_ms / 1000;
        deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
        if (deadline.tv_nsec >= 1000000000L) {
            ++deadline.tv_sec;
            deadline.tv_nsec -= 1000000000L;
        }
    }

    poll_fd.fd = instance->read_fd;
    poll_fd.events = POLLIN;
    poll_fd.revents = 0;
    for (;;) {
        int poll_result = poll(&poll_fd, 1, current_timeout);

        if (poll_result == 0) {
            return NOTIFIER_TIMEOUT;
        }
        if (poll_result == -1) {
            if (errno != EINTR) {
                return NOTIFIER_ERROR;
            }
            if (timeout_ms >= 0) {
                current_timeout = remaining_timeout_ms(&deadline);
                if (current_timeout == -1) {
                    return NOTIFIER_ERROR;
                }
            }
            continue;
        }
        if ((poll_fd.revents & (POLLERR | POLLNVAL)) != 0) {
            errno = EIO;
            return NOTIFIER_ERROR;
        }

        for (;;) {
            unsigned char buffer[128];
            ssize_t count = read(instance->read_fd, buffer, sizeof(buffer));

            if (count > 0) {
                continue;
            }
            if (count == -1 && errno == EINTR) {
                continue;
            }
            if (count == -1 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                break;
            }
            if (count == 0) {
                break;
            }
            return NOTIFIER_ERROR;
        }
        if (atomic_load_explicit(&instance->stopping, memory_order_acquire)) {
            return NOTIFIER_STOPPED;
        }

        /* Signal delivery currently happens in notifier_signal_handler(). */
        if (timeout_ms >= 0) {
            current_timeout = remaining_timeout_ms(&deadline);
            if (current_timeout <= 0) {
                return current_timeout == 0 ? NOTIFIER_TIMEOUT : NOTIFIER_ERROR;
            }
        }
    }
}

int notifier_shutdown(notifier *instance)
{
    sigset_t blocked;
    sigset_t previous_mask;
    unsigned char wake = 'Q';

    if (instance == NULL || !instance->initialized) {
        errno = EINVAL;
        return -1;
    }
    sigemptyset(&blocked);
    sigaddset(&blocked, instance->signum);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous_mask) == -1) {
        return -1;
    }
    if (!atomic_load_explicit(&instance->stopping, memory_order_acquire)) {
        atomic_store_explicit(&instance->stopping, true, memory_order_release);
        if (instance->previous_action_valid) {
            if (sigaction(instance->signum, &instance->previous_action, NULL) == -1) {
                int saved_errno = errno;
                sigprocmask(SIG_SETMASK, &previous_mask, NULL);
                errno = saved_errno;
                return -1;
            }
            instance->previous_action_valid = false;
        }
        active_notifier = NULL;
        while (write(instance->write_fd, &wake, sizeof(wake)) == -1 &&
               errno == EINTR) {
        }
    }
    return sigprocmask(SIG_SETMASK, &previous_mask, NULL);
}

void notifier_destroy(notifier *instance)
{
    if (instance == NULL || !instance->initialized) {
        return;
    }
    if (!atomic_load_explicit(&instance->stopping, memory_order_acquire)) {
        (void)notifier_shutdown(instance);
    }
    close(instance->read_fd);
    close(instance->write_fd);
    instance->initialized = false;
}
