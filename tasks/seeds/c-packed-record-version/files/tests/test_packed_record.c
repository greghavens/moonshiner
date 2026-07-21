#include "packed_record.h"

#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            fprintf(stderr, "%s:%d: check failed: %s\n",                    \
                    __FILE__, __LINE__, #condition);                         \
            return false;                                                    \
        }                                                                    \
    } while (0)

static bool test_legacy_v0_fixture(void)
{
    static const uint8_t bytes[] = {
        0x00,
        0x78, 0x56, 0x34, 0x12,
        0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01,
        0x03, 0x00,
        0xaa, 0xbb, 0xcc
    };
    packed_record record;

    memset(&record, 0xa5, sizeof(record));
    CHECK(packed_record_decode(bytes, sizeof(bytes), &record) == PACKED_OK);
    CHECK(record.record_id == UINT32_C(0x12345678));
    CHECK(record.timestamp == UINT64_C(0x0102030405060708));
    CHECK(record.payload_length == 3);
    CHECK(record.payload == bytes + 15);
    CHECK(memcmp(record.payload, "\xaa\xbb\xcc", 3) == 0);
    return true;
}

static bool test_v1_exact_bytes_and_round_trip(void)
{
    static const uint8_t payload[] = {0xde, 0xad, 0xbe};
    static const uint8_t expected[] = {
        0x01, 0x00, 0x00, 0x00,
        0x11, 0x22, 0x33, 0x44,
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x00, 0x00, 0x00, 0x03,
        0xde, 0xad, 0xbe, 0x00
    };
    const packed_record source = {
        UINT32_C(0x11223344),
        UINT64_C(0x0102030405060708),
        payload,
        sizeof(payload)
    };
    uint8_t encoded[sizeof(expected)];
    packed_record decoded;
    size_t written = 0;

    memset(encoded, 0xa5, sizeof(encoded));
    CHECK(packed_record_encode(&source, encoded, sizeof(encoded), &written) ==
          PACKED_OK);
    CHECK(written == sizeof(expected));
    CHECK(memcmp(encoded, expected, sizeof(expected)) == 0);

    CHECK(packed_record_decode(encoded, written, &decoded) == PACKED_OK);
    CHECK(decoded.record_id == source.record_id);
    CHECK(decoded.timestamp == source.timestamp);
    CHECK(decoded.payload_length == source.payload_length);
    CHECK(memcmp(decoded.payload, source.payload, source.payload_length) == 0);
    return true;
}

static bool test_v1_alignment_for_payload_sizes(void)
{
    uint8_t payload[17];
    uint8_t encoded[40];
    size_t length;

    for (length = 0; length <= sizeof(payload); ++length) {
        packed_record source;
        packed_record decoded;
        size_t written = 0;
        size_t expected = (20 + length + 7) & ~(size_t)7;

        memset(payload, (int)(0x30 + length), sizeof(payload));
        source.record_id = (uint32_t)(100 + length);
        source.timestamp = UINT64_C(0xfedcba9876543210) + length;
        source.payload = payload;
        source.payload_length = length;

        memset(encoded, 0x5a, sizeof(encoded));
        CHECK(packed_record_encode(&source, encoded, sizeof(encoded), &written) ==
              PACKED_OK);
        CHECK(written == expected);
        CHECK((written % 8) == 0);
        CHECK(packed_record_decode(encoded, written, &decoded) == PACKED_OK);
        CHECK(decoded.record_id == source.record_id);
        CHECK(decoded.timestamp == source.timestamp);
        CHECK(decoded.payload_length == length);
        CHECK(memcmp(decoded.payload, payload, length) == 0);
    }
    return true;
}

static bool test_v1_rejects_noncanonical_lengths_and_padding(void)
{
    uint8_t valid[24] = {
        0x01, 0x00, 0x00, 0x00,
        0x01, 0x02, 0x03, 0x04,
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x00, 0x00, 0x00, 0x03,
        0xaa, 0xbb, 0xcc, 0x00
    };
    uint8_t with_trailing_byte[25];
    packed_record untouched = {
        UINT32_C(0xfeedface), UINT64_C(0x1122334455667788), NULL, 77
    };
    packed_record output;

    output = untouched;
    CHECK(packed_record_decode(valid, 23, &output) == PACKED_BAD_FORMAT);
    CHECK(output.record_id == untouched.record_id);
    CHECK(output.timestamp == untouched.timestamp);
    CHECK(output.payload == untouched.payload);
    CHECK(output.payload_length == untouched.payload_length);

    memcpy(with_trailing_byte, valid, sizeof(valid));
    with_trailing_byte[24] = 0;
    CHECK(packed_record_decode(with_trailing_byte,
                               sizeof(with_trailing_byte),
                               &output) == PACKED_BAD_FORMAT);

    valid[23] = 0x7f;
    CHECK(packed_record_decode(valid, sizeof(valid), &output) ==
          PACKED_BAD_FORMAT);
    valid[23] = 0;
    valid[2] = 1;
    CHECK(packed_record_decode(valid, sizeof(valid), &output) ==
          PACKED_BAD_FORMAT);
    return true;
}

static bool test_impossible_and_mismatched_lengths(void)
{
    uint8_t bytes[24] = {0};
    packed_record record;
    size_t written = 999;

    bytes[0] = 1;
    bytes[16] = 0xff;
    bytes[17] = 0xff;
    bytes[18] = 0xff;
    bytes[19] = 0xff;
    CHECK(packed_record_decode(bytes, sizeof(bytes), &record) ==
          PACKED_BAD_FORMAT);

    bytes[0] = 0;
    bytes[13] = 1;
    CHECK(packed_record_decode(bytes, 15, &record) == PACKED_BAD_FORMAT);
    CHECK(packed_record_decode(bytes, 17, &record) == PACKED_BAD_FORMAT);

#if SIZE_MAX > UINT32_MAX
    record.record_id = 0;
    record.timestamp = 0;
    record.payload = bytes;
    record.payload_length = (size_t)UINT32_MAX + 1;
    CHECK(packed_record_encode(&record, bytes, sizeof(bytes), &written) ==
          PACKED_RANGE);
    CHECK(written == 999);
#endif
    return true;
}

static bool test_capacity_and_argument_contracts(void)
{
    static const uint8_t payload[] = {1, 2, 3, 4, 5};
    const packed_record record = {7, 9, payload, sizeof(payload)};
    uint8_t output[32];
    uint8_t before[sizeof(output)];
    size_t written = 0;

    memset(output, 0x6c, sizeof(output));
    memcpy(before, output, sizeof(output));
    CHECK(packed_record_encode(&record, output, 31, &written) ==
          PACKED_NO_SPACE);
    CHECK(written == 32);
    CHECK(memcmp(output, before, sizeof(output)) == 0);
    CHECK(packed_record_encode(&record, NULL, 0, &written) == PACKED_NO_SPACE);
    CHECK(written == 32);
    CHECK(packed_record_encode(NULL, output, sizeof(output), &written) ==
          PACKED_INVALID_ARGUMENT);
    CHECK(packed_record_decode(NULL, 0, (packed_record *)output) ==
          PACKED_INVALID_ARGUMENT);
    CHECK(packed_record_decode(output, sizeof(output), NULL) ==
          PACKED_INVALID_ARGUMENT);
    return true;
}

static bool test_unknown_version(void)
{
    const uint8_t input[] = {2};
    packed_record record;

    CHECK(packed_record_decode(input, sizeof(input), &record) ==
          PACKED_BAD_FORMAT);
    return true;
}

struct test_case {
    const char *name;
    bool (*run)(void);
};

int main(void)
{
    static const struct test_case tests[] = {
        {"legacy v0 fixture", test_legacy_v0_fixture},
        {"v1 exact bytes and round trip", test_v1_exact_bytes_and_round_trip},
        {"v1 alignment for payload sizes", test_v1_alignment_for_payload_sizes},
        {"v1 canonical lengths and padding",
         test_v1_rejects_noncanonical_lengths_and_padding},
        {"impossible and mismatched lengths",
         test_impossible_and_mismatched_lengths},
        {"capacity and argument contracts", test_capacity_and_argument_contracts},
        {"unknown version", test_unknown_version}
    };
    size_t i;

    for (i = 0; i < sizeof(tests) / sizeof(tests[0]); ++i) {
        if (!tests[i].run()) {
            fprintf(stderr, "FAIL: %s\n", tests[i].name);
            return 1;
        }
        printf("PASS: %s\n", tests[i].name);
    }
    return 0;
}
