#define _POSIX_C_SOURCE 200809L

#include "mapped_slice.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

extern int __real_open(const char *path, int flags, ...);
extern int __real_close(int descriptor);
extern void *__real_mmap(void *address, size_t length, int protection,
                         int flags, int descriptor, off_t offset);
extern int __real_munmap(void *address, size_t length);

struct syscall_spy {
    bool enabled;
    bool fail_mmap;
    bool fail_close;
    bool fail_munmap;
    int open_calls;
    int close_calls;
    int mmap_calls;
    int munmap_calls;
    int opened_descriptor;
    int open_flags;
    int protection;
    int mapping_flags;
    off_t mapping_offset;
    size_t mapping_length;
    size_t unmapping_length;
    bool has_active_mapping;
    void *active_mapping;
    size_t active_mapping_length;
};

static struct syscall_spy spy;
static int failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            (void)fprintf(stderr, "check failed at line %d: %s\n",             \
                          __LINE__, #condition);                                \
            failures += 1;                                                      \
        }                                                                       \
    } while (false)

static void begin_spy(void)
{
    (void)memset(&spy, 0, sizeof(spy));
    spy.opened_descriptor = -1;
    spy.enabled = true;
}

static void end_spy(void)
{
    spy.enabled = false;
}

int __wrap_open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    int descriptor;

    if ((flags & O_CREAT) != 0) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        descriptor = __real_open(path, flags, mode);
    } else {
        descriptor = __real_open(path, flags);
    }

    if (spy.enabled) {
        spy.open_calls += 1;
        spy.open_flags = flags;
        spy.opened_descriptor = descriptor;
    }
    return descriptor;
}

int __wrap_close(int descriptor)
{
    const bool is_observed =
        spy.enabled && descriptor == spy.opened_descriptor;
    if (is_observed) {
        spy.close_calls += 1;
    }

    const int result = __real_close(descriptor);
    if (is_observed && spy.fail_close && result == 0) {
        errno = EIO;
        return -1;
    }
    return result;
}

void *__wrap_mmap(void *address, size_t length, int protection,
                  int flags, int descriptor, off_t offset)
{
    if (!spy.enabled) {
        return __real_mmap(address, length, protection, flags,
                           descriptor, offset);
    }

    spy.mmap_calls += 1;
    spy.mapping_length = length;
    spy.protection = protection;
    spy.mapping_flags = flags;
    spy.mapping_offset = offset;
    if (spy.fail_mmap) {
        errno = ENOMEM;
        return MAP_FAILED;
    }

    struct stat file_info;
    if (offset < 0 || fstat(descriptor, &file_info) != 0 ||
        file_info.st_size <= offset) {
        errno = EINVAL;
        return MAP_FAILED;
    }

    const uintmax_t available =
        (uintmax_t)file_info.st_size - (uintmax_t)offset;
    if (available > (uintmax_t)SIZE_MAX) {
        errno = EOVERFLOW;
        return MAP_FAILED;
    }

    /*
     * Supply safe backing through EOF while retaining the length requested by
     * production.  This prevents the known short window from faulting before
     * its arguments can be checked below.
     */
    const size_t safe_length = (size_t)available;
    void *mapping = __real_mmap(NULL, safe_length, PROT_READ, MAP_PRIVATE,
                                descriptor, offset);
    if (mapping != MAP_FAILED) {
        spy.has_active_mapping = true;
        spy.active_mapping = mapping;
        spy.active_mapping_length = safe_length;
    }
    return mapping;
}

int __wrap_munmap(void *address, size_t length)
{
    if (!spy.enabled) {
        return __real_munmap(address, length);
    }

    spy.munmap_calls += 1;
    spy.unmapping_length = length;
    if (!spy.has_active_mapping || address != spy.active_mapping) {
        errno = EINVAL;
        return -1;
    }

    const int result =
        __real_munmap(spy.active_mapping, spy.active_mapping_length);
    spy.has_active_mapping = false;
    if (result != 0) {
        return result;
    }
    if (spy.fail_munmap) {
        errno = EIO;
        return -1;
    }
    return 0;
}

static void check_descriptor_closed(void)
{
    CHECK(spy.opened_descriptor >= 0);
    if (spy.opened_descriptor >= 0) {
        errno = 0;
        CHECK(fcntl(spy.opened_descriptor, F_GETFD) == -1);
        CHECK(errno == EBADF);
    }
}

static void check_read_only_contract(void)
{
    CHECK((spy.open_flags & O_ACCMODE) == O_RDONLY);
    CHECK(spy.protection == PROT_READ);
    CHECK((spy.mapping_flags & (MAP_PRIVATE | MAP_SHARED)) == MAP_PRIVATE);
}

static void test_valid_window(const char *path,
                              const unsigned char *file_data,
                              size_t offset,
                              size_t length,
                              size_t expected_mapping_offset,
                              size_t expected_mapping_length)
{
    unsigned char destination[96];
    CHECK(length + 2U <= sizeof(destination));
    (void)memset(destination, 0xa5, sizeof(destination));

    begin_spy();
    const mapped_slice_status status =
        mapped_slice_read(path, (uint64_t)offset, length, destination + 1U);
    end_spy();

    CHECK(status == MAPPED_SLICE_OK);
    CHECK(spy.open_calls == 1);
    CHECK(spy.close_calls == 1);
    CHECK(spy.mmap_calls == 1);
    CHECK(spy.munmap_calls == 1);
    CHECK(spy.mapping_offset == (off_t)expected_mapping_offset);
    CHECK(spy.mapping_length == expected_mapping_length);
    CHECK(spy.unmapping_length == expected_mapping_length);
    CHECK(!spy.has_active_mapping);
    CHECK(destination[0] == 0xa5U);
    CHECK(destination[length + 1U] == 0xa5U);
    CHECK(memcmp(destination + 1U, file_data + offset, length) == 0);
    check_read_only_contract();
    check_descriptor_closed();
}

static void test_zero_and_range_edges(const char *path, size_t file_size)
{
    begin_spy();
    mapped_slice_status status =
        mapped_slice_read(path, (uint64_t)file_size, 0U, NULL);
    end_spy();
    CHECK(status == MAPPED_SLICE_OK);
    CHECK(spy.open_calls == 1);
    CHECK(spy.close_calls == 1);
    CHECK(spy.mmap_calls == 0);
    check_descriptor_closed();

    unsigned char destination[8];
    (void)memset(destination, 0x6c, sizeof(destination));
    begin_spy();
    status = mapped_slice_read(path, (uint64_t)(file_size - 3U),
                               4U, destination);
    end_spy();
    CHECK(status == MAPPED_SLICE_RANGE);
    CHECK(spy.open_calls == 1);
    CHECK(spy.close_calls == 1);
    CHECK(spy.mmap_calls == 0);
    CHECK(destination[0] == 0x6cU && destination[3] == 0x6cU);
    check_descriptor_closed();

    begin_spy();
    status = mapped_slice_read(path, UINT64_MAX, 2U, destination);
    end_spy();
    CHECK(status == MAPPED_SLICE_RANGE);
    CHECK(spy.mmap_calls == 0);
    CHECK(spy.close_calls == 1);
    check_descriptor_closed();
}

static void test_failure_cleanup(const char *path,
                                 const unsigned char *file_data,
                                 size_t page_size)
{
    unsigned char destination[8];
    (void)memset(destination, 0x37, sizeof(destination));

    begin_spy();
    spy.fail_mmap = true;
    mapped_slice_status status =
        mapped_slice_read(path, (uint64_t)(page_size + 1U),
                          4U, destination);
    end_spy();
    CHECK(status == MAPPED_SLICE_SYSTEM);
    CHECK(spy.mmap_calls == 1);
    CHECK(spy.close_calls == 1);
    CHECK(spy.munmap_calls == 0);
    CHECK(destination[0] == 0x37U);
    check_descriptor_closed();

    begin_spy();
    spy.fail_close = true;
    status = mapped_slice_read(path, (uint64_t)(page_size + 1U),
                               4U, destination);
    end_spy();
    CHECK(status == MAPPED_SLICE_SYSTEM);
    CHECK(spy.mmap_calls == 1);
    CHECK(spy.close_calls == 1);
    CHECK(spy.munmap_calls == 1);
    CHECK(!spy.has_active_mapping);
    CHECK(destination[0] == 0x37U);
    check_descriptor_closed();

    begin_spy();
    spy.fail_munmap = true;
    status = mapped_slice_read(path, (uint64_t)(page_size + 1U),
                               4U, destination);
    end_spy();
    CHECK(status == MAPPED_SLICE_SYSTEM);
    CHECK(spy.close_calls == 1);
    CHECK(spy.munmap_calls == 1);
    CHECK(!spy.has_active_mapping);
    CHECK(memcmp(destination, file_data + page_size + 1U, 4U) == 0);
    check_descriptor_closed();
}

static int write_all(int descriptor,
                     const unsigned char *data,
                     size_t length)
{
    size_t written_total = 0U;
    while (written_total < length) {
        const ssize_t written =
            write(descriptor, data + written_total, length - written_total);
        if (written <= 0) {
            return -1;
        }
        written_total += (size_t)written;
    }
    return 0;
}

int main(void)
{
    const long page_value = sysconf(_SC_PAGE_SIZE);
    if (page_value <= 64 || (uintmax_t)page_value > (uintmax_t)SIZE_MAX - 37U) {
        (void)fprintf(stderr, "unsupported page size\n");
        return 2;
    }
    const size_t page_size = (size_t)page_value;
    const size_t file_size = page_size + 37U;
    unsigned char *file_data = malloc(file_size);
    if (file_data == NULL) {
        return 2;
    }
    for (size_t index = 0U; index < file_size; ++index) {
        file_data[index] = (unsigned char)((index * 29U + 7U) & 0xffU);
    }

    char path[] = "/tmp/mapped-slice-test-XXXXXX";
    const int descriptor = mkstemp(path);
    if (descriptor < 0 || write_all(descriptor, file_data, file_size) != 0 ||
        __real_close(descriptor) != 0) {
        (void)fprintf(stderr, "failed to create fixture\n");
        free(file_data);
        return 2;
    }

    test_valid_window(path, file_data,
                      page_size - 5U, 42U, 0U, file_size);
    test_valid_window(path, file_data,
                      page_size + 3U, 17U, page_size, 20U);
    test_valid_window(path, file_data,
                      page_size, 11U, page_size, 11U);
    test_zero_and_range_edges(path, file_size);
    test_failure_cleanup(path, file_data, page_size);

    (void)unlink(path);
    free(file_data);
    if (failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    return 0;
}
