#include "bounded_slice.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            fprintf(stderr, "%s:%d: check failed: %s\n",                     \
                    __FILE__, __LINE__, #condition);                            \
            return 1;                                                           \
        }                                                                       \
    } while (0)

static int test_invalid_arguments_follow_contract(void)
{
    char output[8] = "kept";

    CHECK(bounded_slice_copy(NULL, sizeof(output), "abc", 3U, 0, 1U) == EINVAL);
    CHECK(bounded_slice_copy(output, 0U, "abc", 3U, 0, 1U) == EINVAL);
    CHECK(strcmp(output, "kept") == 0);

    CHECK(bounded_slice_copy(output, sizeof(output), NULL, 0U, 0, 0U) == EINVAL);
    CHECK(output[0] == '\0');
    return 0;
}

static int test_negative_offset_cannot_bypass_source_bounds(void)
{
    char input[4] = {'X', 'a', 'b', 'c'};
    char output[4] = "old";

    CHECK(bounded_slice_copy(output, sizeof(output), &input[1],
                             sizeof(input) - 1U, -1, 1U) == EINVAL);
    CHECK(output[0] == '\0');
    CHECK(input[0] == 'X');
    return 0;
}

static int test_source_range_errors_clear_output(void)
{
    char output[8] = "old";

    CHECK(bounded_slice_copy(output, sizeof(output), "abc", 3U, 4, 0U) == ERANGE);
    CHECK(output[0] == '\0');

    strcpy(output, "old");
    CHECK(bounded_slice_copy(output, sizeof(output), "abc", 3U, 2, 2U) == ERANGE);
    CHECK(output[0] == '\0');

    strcpy(output, "old");
    CHECK(bounded_slice_copy(output, sizeof(output), "abc", 3U, 0,
                             BOUNDED_SLICE_MAX + 1U) == ERANGE);
    CHECK(output[0] == '\0');
    return 0;
}

static int test_destination_requires_room_for_terminator(void)
{
    static const char source[] = "1234567890abcdef";
    unsigned char output[BOUNDED_SLICE_MAX + 1U];

    memset(output, 'Q', sizeof(output));
    output[BOUNDED_SLICE_MAX] = UINT8_C(0xa5);

    CHECK(bounded_slice_copy((char *)output, BOUNDED_SLICE_MAX, source,
                             BOUNDED_SLICE_MAX, 0,
                             BOUNDED_SLICE_MAX) == ERANGE);
    CHECK(output[0] == '\0');
    CHECK(output[BOUNDED_SLICE_MAX] == UINT8_C(0xa5));
    return 0;
}

static int test_maximum_size_and_empty_slice_succeed(void)
{
    static const char source[] = "1234567890abcdef";
    char maximum[BOUNDED_SLICE_MAX + 1U];
    char empty[1] = {'X'};

    CHECK(bounded_slice_copy(maximum, sizeof(maximum), source,
                             BOUNDED_SLICE_MAX, 0,
                             BOUNDED_SLICE_MAX) == 0);
    CHECK(memcmp(maximum, source, BOUNDED_SLICE_MAX) == 0);
    CHECK(maximum[BOUNDED_SLICE_MAX] == '\0');

    CHECK(bounded_slice_copy(empty, sizeof(empty), source,
                             BOUNDED_SLICE_MAX,
                             (ptrdiff_t)BOUNDED_SLICE_MAX, 0U) == 0);
    CHECK(empty[0] == '\0');
    return 0;
}

int main(void)
{
    int failures = 0;

    failures += test_invalid_arguments_follow_contract();
    failures += test_negative_offset_cannot_bypass_source_bounds();
    failures += test_source_range_errors_clear_output();
    failures += test_destination_requires_room_for_terminator();
    failures += test_maximum_size_and_empty_slice_succeed();

    if (failures != 0) {
        fprintf(stderr, "%d test group(s) failed\n", failures);
        return 1;
    }

    puts("all bounded-slice tests passed");
    return 0;
}
