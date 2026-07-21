#include "snapshot_reader.h"

#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

ssize_t __real_read(int fd, void *buffer, size_t count);
ssize_t __wrap_read(int fd, void *buffer, size_t count);

static volatile sig_atomic_t signal_seen = 0;
static pid_t receiver_pid = (pid_t)-1;
static int incident_stream_fd = -1;
static int release_worker_fd = -1;
static unsigned int incident_read_calls = 0;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    signal_seen = 1;
}

static void child_write_all(int fd, const unsigned char *bytes, size_t length)
{
    size_t written = 0;

    while (written < length) {
        ssize_t result = write(fd, bytes + written, length - written);

        if (result > 0) {
            written += (size_t)result;
        } else if (result < 0 && errno == EINTR) {
            continue;
        } else {
            _exit(111);
        }
    }
}

ssize_t __wrap_read(int fd, void *buffer, size_t count)
{
    if (getpid() != receiver_pid || fd != incident_stream_fd) {
        return __real_read(fd, buffer, count);
    }

    ++incident_read_calls;
    if (incident_read_calls == 2U) {
        const unsigned char release = 1;
        sigset_t wait_mask;

        child_write_all(release_worker_fd, &release, 1);
        if (sigprocmask(SIG_SETMASK, NULL, &wait_mask) == -1) {
            return -1;
        }
        if (sigdelset(&wait_mask, SIGUSR1) == -1) {
            return -1;
        }
        while (signal_seen == 0) {
            if (sigsuspend(&wait_mask) == -1 && errno != EINTR) {
                return -1;
            }
        }
        errno = EINTR;
        return -1;
    }

    return __real_read(fd, buffer, count);
}

static bool file_contains(const char *path, const char *needle)
{
    char contents[2048];
    FILE *file = fopen(path, "rb");
    size_t used;

    if (file == NULL) {
        fprintf(stderr, "cannot open incident evidence %s: %s\n",
                path, strerror(errno));
        return false;
    }
    used = fread(contents, 1, sizeof(contents) - 1, file);
    if (ferror(file) != 0) {
        fprintf(stderr, "cannot read incident evidence %s\n", path);
        (void)fclose(file);
        return false;
    }
    contents[used] = '\0';
    if (fclose(file) != 0) {
        fprintf(stderr, "cannot close incident evidence %s\n", path);
        return false;
    }
    return strstr(contents, needle) != NULL;
}

static bool verify_incident_evidence(const char *receiver_log,
                                     const char *syscall_log)
{
    bool valid = true;

    valid = file_contains(receiver_log, "received=5 expected=8") && valid;
    valid = file_contains(receiver_log, "errno=EINTR") && valid;
    valid = file_contains(receiver_log,
                          "state=FGH12345 expected=ABCDEFGH") && valid;
    valid = file_contains(syscall_log,
                          "read(stream_fd, count=3) = -1 errno=EINTR") && valid;
    valid = file_contains(syscall_log,
                          "read(stream_fd, count=8) = 8 bytes=\"FGH12345\"") &&
            valid;
    if (!valid) {
        fprintf(stderr, "incident evidence no longer describes the repro\n");
    }
    return valid;
}

static void worker_process(int stream_write_fd, int release_read_fd)
{
    static const unsigned char prefix[] = "ABCDE";
    static const unsigned char remainder[] = "FGH12345678";
    unsigned char release;
    ssize_t result;

    child_write_all(stream_write_fd, prefix, sizeof(prefix) - 1);
    do {
        result = __real_read(release_read_fd, &release, 1);
    } while (result < 0 && errno == EINTR);
    if (result != 1 || release != 1) {
        _exit(112);
    }
    if (kill(receiver_pid, SIGUSR1) == -1) {
        _exit(113);
    }
    child_write_all(stream_write_fd, remainder, sizeof(remainder) - 1);
    if (close(stream_write_fd) == -1) {
        _exit(114);
    }
    _exit(0);
}

static bool run_subprocess_reproduction(void)
{
    static const unsigned char expected_first[] = "ABCDEFGH";
    static const unsigned char expected_second[] = "12345678";
    int stream_pipe[2];
    int release_pipe[2];
    struct sigaction action;
    sigset_t blocked;
    sigset_t original_mask;
    pid_t worker;
    unsigned char first[sizeof(expected_first) - 1];
    unsigned char second[sizeof(expected_second) - 1];
    int first_result;
    int second_result;
    int second_errno;
    int worker_status = 0;
    bool passed = true;

    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_signal;
    if (sigemptyset(&action.sa_mask) == -1 ||
        sigaction(SIGUSR1, &action, NULL) == -1 ||
        sigemptyset(&blocked) == -1 ||
        sigaddset(&blocked, SIGUSR1) == -1 ||
        sigprocmask(SIG_BLOCK, &blocked, &original_mask) == -1) {
        perror("signal setup");
        return false;
    }
    if (pipe(stream_pipe) == -1 || pipe(release_pipe) == -1) {
        perror("pipe");
        (void)sigprocmask(SIG_SETMASK, &original_mask, NULL);
        return false;
    }

    receiver_pid = getpid();
    incident_stream_fd = stream_pipe[0];
    release_worker_fd = release_pipe[1];
    worker = fork();
    if (worker == -1) {
        perror("fork");
        (void)sigprocmask(SIG_SETMASK, &original_mask, NULL);
        return false;
    }
    if (worker == 0) {
        (void)close(stream_pipe[0]);
        (void)close(release_pipe[1]);
        worker_process(stream_pipe[1], release_pipe[0]);
    }

    (void)close(stream_pipe[1]);
    (void)close(release_pipe[0]);
    memset(first, '?', sizeof(first));
    memset(second, '?', sizeof(second));
    first_result = snapshot_read_exact(stream_pipe[0], first, sizeof(first));
    second_result = snapshot_read_exact(stream_pipe[0], second, sizeof(second));
    second_errno = errno;

    if (waitpid(worker, &worker_status, 0) != worker) {
        perror("waitpid");
        passed = false;
    }
    if (close(stream_pipe[0]) == -1 || close(release_pipe[1]) == -1) {
        perror("close");
        passed = false;
    }
    if (sigprocmask(SIG_SETMASK, &original_mask, NULL) == -1) {
        perror("restore signal mask");
        passed = false;
    }

    printf("observed first=%.*s result=%d second=%.*s result=%d errno=%d\n",
           (int)sizeof(first), (const char *)first, first_result,
           (int)sizeof(second), (const char *)second, second_result,
           second_errno);

    if (signal_seen == 0) {
        fprintf(stderr, "FAIL: signal did not interrupt the read boundary\n");
        passed = false;
    }
    if (!WIFEXITED(worker_status) || WEXITSTATUS(worker_status) != 0) {
        fprintf(stderr, "FAIL: worker subprocess status=%d\n", worker_status);
        passed = false;
    }
    if (first_result != 0 ||
        memcmp(first, expected_first, sizeof(first)) != 0) {
        fprintf(stderr,
                "FAIL: first snapshot crossed a stream boundary; expected %s\n",
                expected_first);
        passed = false;
    }
    if (second_result != 0 ||
        memcmp(second, expected_second, sizeof(second)) != 0) {
        fprintf(stderr,
                "FAIL: second snapshot was shifted or truncated; expected %s\n",
                expected_second);
        passed = false;
    }
    return passed;
}

static bool verify_argument_contract(void)
{
    unsigned char bytes[4];
    int short_pipe[2];

    if (snapshot_read_exact(-1, NULL, 0) != 0) {
        fprintf(stderr, "FAIL: zero-length read must succeed\n");
        return false;
    }
    errno = 0;
    if (snapshot_read_exact(-1, NULL, 1) != -1 || errno != EINVAL) {
        fprintf(stderr, "FAIL: NULL destination must fail with EINVAL\n");
        return false;
    }
    errno = 0;
    if (snapshot_read_exact(-1, bytes, 1) != -1 || errno != EBADF) {
        fprintf(stderr, "FAIL: non-EINTR read error must retain errno\n");
        return false;
    }
    if (pipe(short_pipe) == -1) {
        perror("contract pipe");
        return false;
    }
    child_write_all(short_pipe[1], (const unsigned char *)"xyz", 3);
    if (close(short_pipe[1]) == -1) {
        perror("contract pipe close");
        (void)close(short_pipe[0]);
        return false;
    }
    errno = 0;
    if (snapshot_read_exact(short_pipe[0], bytes, sizeof(bytes)) != -1 ||
        errno != ECONNRESET) {
        fprintf(stderr, "FAIL: early EOF must fail with ECONNRESET\n");
        (void)close(short_pipe[0]);
        return false;
    }
    if (close(short_pipe[0]) == -1) {
        perror("contract pipe close");
        return false;
    }
    return true;
}

int main(int argc, char **argv)
{
    bool passed;

    if (argc != 3) {
        fprintf(stderr, "usage: %s RECEIVER_LOG SYSCALL_LOG\n", argv[0]);
        return 2;
    }
    passed = verify_incident_evidence(argv[1], argv[2]);
    passed = verify_argument_contract() && passed;
    passed = run_subprocess_reproduction() && passed;
    if (!passed) {
        return 1;
    }
    puts("PASS: interrupted exact read preserves partial progress");
    return 0;
}
