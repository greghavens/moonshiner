#define _POSIX_C_SOURCE 200809L

#include "atomic_file.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static int check(int condition, const char *message)
{
    if (!condition) {
        (void)fprintf(stderr, "FAIL: %s\n", message);
        return 0;
    }
    return 1;
}

static char *make_path(const char *directory, const char *name)
{
    size_t directory_length = strlen(directory);
    size_t name_length = strlen(name);
    char *path;

    if (directory_length > SIZE_MAX - name_length - 2U) {
        return NULL;
    }

    path = malloc(directory_length + name_length + 2U);
    if (path == NULL) {
        return NULL;
    }
    (void)memcpy(path, directory, directory_length);
    path[directory_length] = '/';
    (void)memcpy(path + directory_length + 1U, name, name_length + 1U);
    return path;
}

static int store_bytes(const char *path, const char *bytes)
{
    size_t length = strlen(bytes);
    size_t offset = 0U;
    int descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC,
                          S_IRUSR | S_IWUSR);

    if (descriptor < 0) {
        return 0;
    }
    while (offset < length) {
        ssize_t written = write(descriptor, bytes + offset, length - offset);

        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            (void)close(descriptor);
            return 0;
        }
        offset += (size_t)written;
    }
    return close(descriptor) == 0;
}

static int file_equals(const char *path, const char *expected)
{
    size_t expected_length = strlen(expected);
    char buffer[128];
    size_t offset = 0U;
    int descriptor = open(path, O_RDONLY);

    if (descriptor < 0) {
        return 0;
    }
    for (;;) {
        ssize_t received = read(descriptor, buffer + offset, sizeof(buffer) - offset);

        if (received < 0 && errno == EINTR) {
            continue;
        }
        if (received < 0) {
            (void)close(descriptor);
            return 0;
        }
        if (received == 0) {
            break;
        }
        offset += (size_t)received;
        if (offset == sizeof(buffer)) {
            char extra;
            ssize_t extra_length = read(descriptor, &extra, 1U);

            if (extra_length != 0) {
                (void)close(descriptor);
                return 0;
            }
            break;
        }
    }

    if (close(descriptor) != 0) {
        return 0;
    }
    return offset == expected_length && memcmp(buffer, expected, offset) == 0;
}

static char *predictable_path(const char *destination)
{
    size_t capacity = strlen(destination) + 64U;
    char *path = malloc(capacity);
    int written;

    if (path == NULL) {
        return NULL;
    }
    written = snprintf(path, capacity, "%s.tmp.%ld", destination,
                       (long)getpid());
    if (written < 0 || (size_t)written >= capacity) {
        free(path);
        return NULL;
    }
    return path;
}

static int no_temporary_entries(const char *directory, const char *prefix)
{
    DIR *stream = opendir(directory);
    struct dirent *entry;
    size_t prefix_length = strlen(prefix);

    if (stream == NULL) {
        return 0;
    }
    while ((entry = readdir(stream)) != NULL) {
        if (strncmp(entry->d_name, prefix, prefix_length) == 0) {
            (void)closedir(stream);
            return 0;
        }
    }
    return closedir(stream) == 0;
}

static int test_preplaced_symlink(const char *directory)
{
    static const char original[] = "protected victim contents";
    static const char previous[] = "previous destination contents";
    static const char replacement[] = "complete replacement contents";
    char *destination = make_path(directory, "output");
    char *victim = make_path(directory, "victim");
    char *attack_path = NULL;
    struct stat status;
    mode_t old_mask;
    int result = 0;

    if (!check(destination != NULL && victim != NULL, "path allocation failed")) {
        goto done;
    }
    attack_path = predictable_path(destination);
    if (!check(attack_path != NULL, "attack path allocation failed") ||
        !check(store_bytes(victim, original), "could not create victim") ||
        !check(store_bytes(destination, previous), "could not create old destination") ||
        !check(symlink(victim, attack_path) == 0, "could not pre-place symlink")) {
        goto done;
    }

    old_mask = umask((mode_t)0077);
    result = atomic_file_write(destination, replacement, strlen(replacement),
                               (mode_t)0640);
    (void)umask(old_mask);
    if (!check(result == 0, "safe atomic replacement must succeed") ||
        !check(file_equals(victim, original), "predictable temp path modified victim") ||
        !check(file_equals(destination, replacement), "published contents differ") ||
        !check(lstat(destination, &status) == 0 && S_ISREG(status.st_mode),
               "destination must be a regular file, not the planted symlink") ||
        !check((status.st_mode & (mode_t)0777) == (mode_t)0640,
               "published permissions must ignore the process umask")) {
        result = 0;
        goto done;
    }

    result = 1;

done:
    if (attack_path != NULL && unlink(attack_path) != 0 && errno != ENOENT) {
        result = 0;
    }
    free(attack_path);
    free(victim);
    free(destination);
    return result;
}

static int test_failure_cleanup(const char *directory)
{
    static const char contents[] = "never published";
    char *occupied = make_path(directory, "occupied");
    int call_result;
    int result = 0;

    if (!check(occupied != NULL, "path allocation failed") ||
        !check(mkdir(occupied, (mode_t)0700) == 0, "could not create occupied path")) {
        goto done;
    }

    call_result = atomic_file_write(occupied, contents, strlen(contents),
                                    (mode_t)0600);
    if (!check(call_result != 0, "renaming over a directory must fail") ||
        !check(no_temporary_entries(directory, "occupied.tmp."),
               "failed publication leaked a temporary file")) {
        goto done;
    }

    result = 1;

done:
    if (occupied != NULL && rmdir(occupied) != 0 && errno != ENOENT) {
        result = 0;
    }
    free(occupied);
    return result;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        (void)fprintf(stderr, "usage: runtime_test DIRECTORY\n");
        return 2;
    }

    if (!test_preplaced_symlink(argv[1]) || !test_failure_cleanup(argv[1])) {
        return 1;
    }

    (void)puts("all runtime checks passed");
    return 0;
}
