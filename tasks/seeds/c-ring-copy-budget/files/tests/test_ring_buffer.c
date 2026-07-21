#include "ring_buffer.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
static size_t observed_malloc_calls;
static size_t observed_memcpy_bytes;
static size_t observed_memcpy_calls;
static void *observed_memcpy_destinations[4];
static const void *observed_memcpy_sources[4];
static size_t observed_memcpy_lengths[4];

void *__real_malloc(size_t size);
void *__real_memcpy(void *destination, const void *source, size_t length);

void *__wrap_malloc(size_t size)
{
    ++observed_malloc_calls;
    return __real_malloc(size);
}

void *__wrap_memcpy(void *destination, const void *source, size_t length)
{
    if (observed_memcpy_calls < 4U) {
        observed_memcpy_destinations[observed_memcpy_calls] = destination;
        observed_memcpy_sources[observed_memcpy_calls] = source;
        observed_memcpy_lengths[observed_memcpy_calls] = length;
    }
    ++observed_memcpy_calls;
    observed_memcpy_bytes += length;
    return __real_memcpy(destination, source, length);
}

#define CHECK(condition) check((condition), #condition, __FILE__, __LINE__)

static void check(bool condition, const char *expression, const char *file,
                  int line)
{
    if (!condition) {
        (void)fprintf(stderr, "%s:%d: check failed: %s\n", file, line,
                      expression);
        ++failures;
    }
}

static void test_contiguous_read_has_one_byte_move_per_byte(void)
{
    RingBuffer ring;
    uint8_t storage[8];
    uint8_t output[4] = {0U};
    static const uint8_t input[] = {'a', 'b', 'c', 'd'};

    CHECK(ring_buffer_init(&ring, storage, sizeof(storage)));
    CHECK(ring_buffer_write(&ring, input, sizeof(input)) == sizeof(input));
    ring_buffer_reset_reader_bytes_moved(&ring);
    observed_malloc_calls = 0U;
    observed_memcpy_bytes = 0U;
    observed_memcpy_calls = 0U;

    CHECK(ring_buffer_read(&ring, output, 3U) == 3U);
    CHECK(memcmp(output, "abc", 3U) == 0);
    CHECK(ring_buffer_reader_bytes_moved(&ring) == 3U);
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == 3U);
    CHECK(observed_memcpy_calls == 1U);
    CHECK(observed_memcpy_destinations[0] == output);
    CHECK(observed_memcpy_sources[0] == storage);
    CHECK(observed_memcpy_lengths[0] == 3U);
    CHECK(ring_buffer_size(&ring) == 1U);

    observed_malloc_calls = 0U;
    observed_memcpy_bytes = 0U;
    observed_memcpy_calls = 0U;
    CHECK(ring_buffer_read(&ring, output, sizeof(output)) == 1U);
    CHECK(output[0] == (uint8_t)'d');
    CHECK(ring_buffer_reader_bytes_moved(&ring) == 4U);
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == 1U);
    CHECK(observed_memcpy_calls == 1U);
    CHECK(observed_memcpy_destinations[0] == output);
    CHECK(observed_memcpy_sources[0] == storage + 3U);
    CHECK(observed_memcpy_lengths[0] == 1U);
    CHECK(ring_buffer_size(&ring) == 0U);
}

static void test_wrapped_read_has_one_byte_move_per_byte(void)
{
    RingBuffer ring;
    uint8_t storage[5];
    uint8_t discarded[3];
    uint8_t output[5] = {0U};
    static const uint8_t first_input[] = {'A', 'B', 'C', 'D'};
    static const uint8_t second_input[] = {'E', 'F', 'G', 'H'};

    CHECK(ring_buffer_init(&ring, storage, sizeof(storage)));
    CHECK(ring_buffer_write(&ring, first_input, sizeof(first_input)) ==
          sizeof(first_input));
    CHECK(ring_buffer_read(&ring, discarded, sizeof(discarded)) ==
          sizeof(discarded));
    CHECK(ring_buffer_write(&ring, second_input, sizeof(second_input)) ==
          sizeof(second_input));
    CHECK(ring_buffer_size(&ring) == sizeof(storage));
    ring_buffer_reset_reader_bytes_moved(&ring);
    observed_malloc_calls = 0U;
    observed_memcpy_bytes = 0U;
    observed_memcpy_calls = 0U;

    CHECK(ring_buffer_read(&ring, output, sizeof(output)) == sizeof(output));
    CHECK(memcmp(output, "DEFGH", sizeof(output)) == 0);
    CHECK(ring_buffer_reader_bytes_moved(&ring) == sizeof(output));
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == sizeof(output));
    CHECK(observed_memcpy_calls == 2U);
    CHECK(observed_memcpy_destinations[0] == output);
    CHECK(observed_memcpy_sources[0] == storage + 3U);
    CHECK(observed_memcpy_lengths[0] == 2U);
    CHECK(observed_memcpy_destinations[1] == output + 2U);
    CHECK(observed_memcpy_sources[1] == storage);
    CHECK(observed_memcpy_lengths[1] == 3U);
    CHECK(ring_buffer_size(&ring) == 0U);
}

static void test_zero_length_read_accepts_null_and_changes_nothing(void)
{
    RingBuffer ring;
    uint8_t storage[4];
    uint8_t prefix = 0U;
    uint8_t output[2] = {0U};
    static const uint8_t input[] = {'w', 'x', 'y'};

    CHECK(ring_buffer_init(&ring, storage, sizeof(storage)));
    CHECK(ring_buffer_write(&ring, input, sizeof(input)) == sizeof(input));
    CHECK(ring_buffer_read(&ring, &prefix, 1U) == 1U);
    CHECK(prefix == (uint8_t)'w');
    observed_malloc_calls = 0U;
    observed_memcpy_bytes = 0U;
    observed_memcpy_calls = 0U;

    CHECK(ring_buffer_read(&ring, NULL, 0U) == 0U);
    CHECK(ring_buffer_size(&ring) == sizeof(output));
    CHECK(ring_buffer_reader_bytes_moved(&ring) == 1U);
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == 0U);
    CHECK(observed_memcpy_calls == 0U);
    CHECK(ring_buffer_read(&ring, output, sizeof(output)) == sizeof(output));
    CHECK(memcmp(output, input + 1U, sizeof(output)) == 0);
    CHECK(ring_buffer_reader_bytes_moved(&ring) == sizeof(input));
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == sizeof(output));
    CHECK(observed_memcpy_calls == 1U);
    CHECK(observed_memcpy_destinations[0] == output);
    CHECK(observed_memcpy_sources[0] == storage + 1U);
    CHECK(observed_memcpy_lengths[0] == sizeof(output));
}

static void test_destination_and_storage_remain_caller_owned(void)
{
    RingBuffer ring;
    uint8_t storage[6];
    uint8_t *allocation;
    uint8_t follow_up = 0U;
    static const uint8_t input[] = {'m', 'n', 'o'};
    static const uint8_t later[] = {'p'};

    allocation = malloc(5U);
    CHECK(allocation != NULL);
    if (allocation == NULL) {
        return;
    }
    (void)memset(allocation, 0xA5, 5U);

    CHECK(ring_buffer_init(&ring, storage, sizeof(storage)));
    CHECK(ring_buffer_write(&ring, input, sizeof(input)) == sizeof(input));
    ring_buffer_reset_reader_bytes_moved(&ring);
    observed_malloc_calls = 0U;
    observed_memcpy_bytes = 0U;
    observed_memcpy_calls = 0U;
    CHECK(ring_buffer_read(&ring, allocation + 1U, sizeof(input)) ==
          sizeof(input));
    CHECK(allocation[0] == 0xA5U);
    CHECK(allocation[4] == 0xA5U);
    CHECK(memcmp(allocation + 1U, input, sizeof(input)) == 0);
    CHECK(ring_buffer_reader_bytes_moved(&ring) == sizeof(input));
    CHECK(observed_malloc_calls == 0U);
    CHECK(observed_memcpy_bytes == sizeof(input));
    CHECK(observed_memcpy_calls == 1U);
    CHECK(observed_memcpy_destinations[0] == allocation + 1U);
    CHECK(observed_memcpy_sources[0] == storage);
    CHECK(observed_memcpy_lengths[0] == sizeof(input));

    allocation[1] = (uint8_t)'z';
    free(allocation);

    storage[5] = 0x5AU;
    CHECK(ring_buffer_write(&ring, later, sizeof(later)) == sizeof(later));
    CHECK(ring_buffer_read(&ring, &follow_up, 1U) == 1U);
    CHECK(follow_up == (uint8_t)'p');
    CHECK(storage[5] == 0x5AU);
}

int main(void)
{
    test_contiguous_read_has_one_byte_move_per_byte();
    test_wrapped_read_has_one_byte_move_per_byte();
    test_zero_length_read_accepts_null_and_changes_nothing();
    test_destination_and_storage_remain_caller_owned();

    if (failures != 0) {
        (void)fprintf(stderr, "%d test check(s) failed\n", failures);
        return EXIT_FAILURE;
    }

    (void)puts("all ring-buffer tests passed");
    return EXIT_SUCCESS;
}
