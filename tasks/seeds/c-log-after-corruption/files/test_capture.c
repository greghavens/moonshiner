#include "capture.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__,           \
                    #condition);                                               \
            failures++;                                                        \
        }                                                                      \
    } while (0)

static int guard_is_unchanged(const unsigned char *guard, size_t length) {
    size_t index;
    for (index = 0; index < length; index++) {
        if (guard[index] != 0xa5U) {
            return 0;
        }
    }
    return 1;
}

static void test_zero_capacity_performs_no_write(void) {
    unsigned char destination[4] = {0xa5U, 0xa5U, 0xa5U, 0xa5U};

    CHECK(capture_copy_source((char *)destination, 0, "ignored") == 0);
    CHECK(guard_is_unchanged(destination, sizeof(destination)));
}

static void test_capacity_one_stores_only_nul(void) {
    unsigned char guarded[1 + 4];
    guarded[0] = 0xccU;
    memset(guarded + 1, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, 1, "x") == 0);
    CHECK(guarded[0] == '\0');
    CHECK(guard_is_unchanged(guarded + 1, 4));
}

static void test_small_capacity_reserves_terminator(void) {
    enum { capacity = 5 };
    unsigned char guarded[capacity + 4];
    memset(guarded, 0xcc, capacity);
    memset(guarded + capacity, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, capacity, "abcdef") == 4);
    CHECK(memcmp(guarded, "abcd\0", capacity) == 0);
    CHECK(guard_is_unchanged(guarded + capacity, 4));
}

static void test_short_source_returns_its_length(void) {
    enum { capacity = 5 };
    const char source[capacity] = {'x', 'y', '\0', 'q', 'r'};
    unsigned char guarded[capacity + 4];
    memset(guarded, 0xcc, capacity);
    memset(guarded + capacity, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, capacity, source) == 2);
    CHECK(strcmp((const char *)guarded, "xy") == 0);
    CHECK(guard_is_unchanged(guarded + capacity, 4));
}

static void test_short_source_uses_last_byte_for_nul(void) {
    unsigned char guarded[CAPTURE_SOURCE_CAPACITY + 4];
    memset(guarded, 0xcc, CAPTURE_SOURCE_CAPACITY);
    memset(guarded + CAPTURE_SOURCE_CAPACITY, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, CAPTURE_SOURCE_CAPACITY,
                              "north-senso") == 11);
    CHECK(strcmp((const char *)guarded, "north-senso") == 0);
    CHECK(guard_is_unchanged(guarded + CAPTURE_SOURCE_CAPACITY, 4));
}

static void test_exact_capacity_source_preserves_guard(void) {
    unsigned char guarded[CAPTURE_SOURCE_CAPACITY + 4];
    memset(guarded, 0xcc, CAPTURE_SOURCE_CAPACITY);
    memset(guarded + CAPTURE_SOURCE_CAPACITY, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, CAPTURE_SOURCE_CAPACITY,
                              "north-sensor") == 11);
    CHECK(memcmp(guarded, "north-senso", 11) == 0);
    CHECK(guarded[CAPTURE_SOURCE_CAPACITY - 1] == '\0');
    CHECK(guard_is_unchanged(guarded + CAPTURE_SOURCE_CAPACITY, 4));
}

static void test_overlong_source_preserves_guard(void) {
    unsigned char guarded[CAPTURE_SOURCE_CAPACITY + 4];
    memset(guarded, 0xcc, CAPTURE_SOURCE_CAPACITY);
    memset(guarded + CAPTURE_SOURCE_CAPACITY, 0xa5, 4);

    CHECK(capture_copy_source((char *)guarded, CAPTURE_SOURCE_CAPACITY,
                              "north-sensor-secondary") == 11);
    CHECK(strcmp((const char *)guarded, "north-senso") == 0);
    CHECK(guard_is_unchanged(guarded + CAPTURE_SOURCE_CAPACITY, 4));
}

static void test_record_initialization_does_not_corrupt_checksum(void) {
    CaptureRecord record;
    char diagnostic[CAPTURE_DIAGNOSTIC_CAPACITY] = "stale diagnostic";

    capture_record_init(&record, "north-sensor", "9f2c7a10");

    CHECK(strcmp(capture_record_source(&record), "north-senso") == 0);
    CHECK(strcmp(capture_record_expected_checksum(&record), "9f2c7a10") == 0);
    CHECK(capture_record_verify(&record, "9f2c7a10", diagnostic,
                                sizeof(diagnostic)) == 1);
    CHECK(diagnostic[0] == '\0');
}

static void test_real_mismatch_keeps_precise_diagnostic(void) {
    CaptureRecord record;
    char diagnostic[CAPTURE_DIAGNOSTIC_CAPACITY];
    const char expected[] =
        "checksum mismatch after capture: source=\"dock-7\" "
        "expected=9f2c7a10 actual=9f2c7a11\n";

    capture_record_init(&record, "dock-7", "9f2c7a10");

    CHECK(capture_record_verify(&record, "9f2c7a11", diagnostic,
                                sizeof(diagnostic)) == 0);
    CHECK(strcmp(diagnostic, expected) == 0);
}

int main(void) {
    test_zero_capacity_performs_no_write();
    test_capacity_one_stores_only_nul();
    test_small_capacity_reserves_terminator();
    test_short_source_returns_its_length();
    test_short_source_uses_last_byte_for_nul();
    test_exact_capacity_source_preserves_guard();
    test_overlong_source_preserves_guard();
    test_record_initialization_does_not_corrupt_checksum();
    test_real_mismatch_keeps_precise_diagnostic();

    if (failures != 0) {
        fprintf(stderr, "%d assertion(s) failed\n", failures);
        return 1;
    }

    puts("all capture tests passed");
    return 0;
}
