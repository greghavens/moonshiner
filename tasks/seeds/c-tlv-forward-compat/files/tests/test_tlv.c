#include "tlv.h"

#include <stdio.h>
#include <string.h>

static int failures;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                     \
            fprintf(stderr, "%s:%d: check failed: %s\n",                     \
                    __FILE__, __LINE__, #condition);                            \
            failures++;                                                        \
        }                                                                      \
    } while (0)

static void check_message_is_empty(const struct tlv_message *message)
{
    CHECK(!message->has_kind);
    CHECK(!message->has_sequence);
    CHECK(!message->has_flags);
    CHECK(!message->has_body);
    CHECK(message->body_len == 0u);
    CHECK(message->unknown_fields == NULL);
    CHECK(message->unknown_fields_len == 0u);
}

static void test_known_message_is_canonical(void)
{
    const uint8_t expected[] = {
        0x01, 0x00, 0x01, 0x7f,
        0x02, 0x00, 0x04, 0x01, 0x02, 0x03, 0x04,
        0x03, 0x00, 0x02, 0xa1, 0xb2,
        0x04, 0x00, 0x03, 0xde, 0xad, 0xbe
    };
    struct tlv_message message;
    uint8_t output[64];
    size_t written = 0;

    tlv_message_init(&message);
    message.has_body = true;
    message.body_len = 3;
    memcpy(message.body, "\xde\xad\xbe", 3);
    message.has_flags = true;
    message.flags = 0xa1b2;
    message.has_kind = true;
    message.kind = 0x7f;
    message.has_sequence = true;
    message.sequence = 0x01020304;

    CHECK(tlv_serialize(&message, output, sizeof(output), &written) == TLV_OK);
    CHECK(written == sizeof(expected));
    CHECK(memcmp(output, expected, sizeof(expected)) == 0);

    CHECK(tlv_parse(expected, sizeof(expected), &message) == TLV_OK);
    CHECK(message.has_kind && message.kind == 0x7f);
    CHECK(message.has_sequence && message.sequence == 0x01020304);
    CHECK(message.has_flags && message.flags == 0xa1b2);
    CHECK(message.has_body && message.body_len == 3u);
    if (message.has_body && message.body_len == 3u) {
        CHECK(memcmp(message.body, "\xde\xad\xbe", 3u) == 0);
    }
    CHECK(message.unknown_fields == NULL);
    CHECK(message.unknown_fields_len == 0u);
    tlv_message_free(&message);
}

static void test_unknown_records_are_preserved(void)
{
    const uint8_t wire[] = {
        0x80, 0x00, 0x02, 0xaa, 0xbb,
        0x02, 0x00, 0x04, 0x10, 0x20, 0x30, 0x40,
        0x01, 0x00, 0x01, 0x05,
        0x80, 0x00, 0x01, 0xcc,
        0x81, 0x00, 0x00
    };
    const uint8_t unknown[] = {
        0x80, 0x00, 0x02, 0xaa, 0xbb,
        0x80, 0x00, 0x01, 0xcc,
        0x81, 0x00, 0x00
    };
    const uint8_t canonical[] = {
        0x01, 0x00, 0x01, 0x05,
        0x02, 0x00, 0x04, 0x10, 0x20, 0x30, 0x40,
        0x80, 0x00, 0x02, 0xaa, 0xbb,
        0x80, 0x00, 0x01, 0xcc,
        0x81, 0x00, 0x00
    };
    struct tlv_message message;
    uint8_t output[64];
    uint8_t short_output[sizeof(canonical) - 1u];
    uint8_t untouched[sizeof(short_output)];
    size_t written = 0;

    memset(short_output, 0xa5, sizeof(short_output));
    memcpy(untouched, short_output, sizeof(untouched));
    tlv_message_init(&message);
    CHECK(tlv_parse(wire, sizeof(wire), &message) == TLV_OK);
    CHECK(message.has_kind && message.kind == 0x05);
    CHECK(message.has_sequence && message.sequence == 0x10203040);
    CHECK(message.unknown_fields_len == sizeof(unknown));
    if (message.unknown_fields_len == sizeof(unknown)) {
        CHECK(memcmp(message.unknown_fields, unknown, sizeof(unknown)) == 0);
    }
    CHECK(tlv_serialize(&message, short_output, sizeof(short_output), &written) ==
          TLV_ERR_SPACE);
    CHECK(written == sizeof(canonical));
    CHECK(memcmp(short_output, untouched, sizeof(short_output)) == 0);
    CHECK(tlv_serialize(&message, output, sizeof(output), &written) == TLV_OK);
    CHECK(written == sizeof(canonical));
    if (written == sizeof(canonical)) {
        CHECK(memcmp(output, canonical, sizeof(canonical)) == 0);
    }
    tlv_message_free(&message);
}

static void test_malformed_lengths_are_rejected(void)
{
    const uint8_t one_byte_header[] = {0x01};
    const uint8_t partial_header[] = {0x01, 0x00};
    const uint8_t short_value[] = {0x02, 0x00, 0x04, 0x01, 0x02, 0x03};
    const uint8_t wrong_kind_length[] = {0x01, 0x00, 0x02, 0x01, 0x02};
    const uint8_t wrong_sequence_length[] = {0x02, 0x00, 0x03, 1, 2, 3};
    const uint8_t wrong_flags_length[] = {0x03, 0x00, 0x01, 1};
    uint8_t oversized_body[3u + TLV_MAX_BODY + 1u] = {
        TLV_TAG_BODY, 0x04, 0x01
    };
    struct tlv_message message;

    tlv_message_init(&message);
    CHECK(tlv_parse(one_byte_header, sizeof(one_byte_header), &message) ==
          TLV_ERR_TRUNCATED);
    check_message_is_empty(&message);
    CHECK(tlv_parse(partial_header, sizeof(partial_header), &message) ==
          TLV_ERR_TRUNCATED);
    check_message_is_empty(&message);
    CHECK(tlv_parse(short_value, sizeof(short_value), &message) ==
          TLV_ERR_TRUNCATED);
    check_message_is_empty(&message);
    CHECK(tlv_parse(wrong_kind_length, sizeof(wrong_kind_length), &message) ==
          TLV_ERR_LENGTH);
    check_message_is_empty(&message);
    CHECK(tlv_parse(wrong_sequence_length, sizeof(wrong_sequence_length),
                    &message) == TLV_ERR_LENGTH);
    check_message_is_empty(&message);
    CHECK(tlv_parse(wrong_flags_length, sizeof(wrong_flags_length), &message) ==
          TLV_ERR_LENGTH);
    check_message_is_empty(&message);
    CHECK(tlv_parse(oversized_body, sizeof(oversized_body), &message) ==
          TLV_ERR_LENGTH);
    check_message_is_empty(&message);
    tlv_message_free(&message);
}

static void test_parse_errors_are_transactional(void)
{
    const uint8_t malformed[] = {
        0x01, 0x00, 0x01, 0x11,
        0x80, 0x00, 0x02, 0xaa, 0xbb,
        0x03, 0x00, 0x01, 0xff
    };
    struct tlv_message message;

    tlv_message_init(&message);
    message.has_sequence = true;
    message.sequence = 42u;
    CHECK(tlv_parse(malformed, sizeof(malformed), &message) == TLV_ERR_LENGTH);
    check_message_is_empty(&message);
    tlv_message_free(&message);
}

static void test_all_known_duplicates_are_rejected(void)
{
    const uint8_t duplicate_kind[] = {
        0x01, 0x00, 0x01, 0x11, 0x01, 0x00, 0x01, 0x22
    };
    const uint8_t duplicate_sequence[] = {
        0x02, 0x00, 0x04, 0, 0, 0, 1,
        0x02, 0x00, 0x04, 0, 0, 0, 2
    };
    const uint8_t duplicate_flags[] = {
        0x03, 0x00, 0x02, 0, 1, 0x03, 0x00, 0x02, 0, 2
    };
    const uint8_t duplicate_body[] = {
        0x04, 0x00, 0x00, 0x04, 0x00, 0x01, 0xaa
    };
    struct {
        const uint8_t *wire;
        size_t wire_len;
    } cases[] = {
        {duplicate_kind, sizeof(duplicate_kind)},
        {duplicate_sequence, sizeof(duplicate_sequence)},
        {duplicate_flags, sizeof(duplicate_flags)},
        {duplicate_body, sizeof(duplicate_body)}
    };
    struct tlv_message message;
    size_t index;

    tlv_message_init(&message);
    for (index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        CHECK(tlv_parse(cases[index].wire, cases[index].wire_len, &message) ==
              TLV_ERR_DUPLICATE);
        check_message_is_empty(&message);
    }
    tlv_message_free(&message);
}

static void test_parse_replaces_previous_contents(void)
{
    const uint8_t first[] = {0x90, 0x00, 0x01, 0xaa};
    const uint8_t second[] = {0x01, 0x00, 0x01, 0x07};
    struct tlv_message message;

    tlv_message_init(&message);
    CHECK(tlv_parse(first, sizeof(first), &message) == TLV_OK);
    CHECK(message.unknown_fields_len == sizeof(first));
    CHECK(tlv_parse(second, sizeof(second), &message) == TLV_OK);
    CHECK(message.has_kind && message.kind == 7u);
    CHECK(message.unknown_fields == NULL);
    CHECK(message.unknown_fields_len == 0u);
    tlv_message_free(&message);
}

static void test_short_output_is_not_modified(void)
{
    struct tlv_message message;
    uint8_t output[4] = {0xa5, 0xa5, 0xa5, 0xa5};
    const uint8_t untouched[4] = {0xa5, 0xa5, 0xa5, 0xa5};
    size_t written = 0;

    tlv_message_init(&message);
    message.has_kind = true;
    message.kind = 3;
    message.has_sequence = true;
    message.sequence = 9;

    CHECK(tlv_serialize(&message, output, sizeof(output), &written) ==
          TLV_ERR_SPACE);
    CHECK(written == 11);
    CHECK(memcmp(output, untouched, sizeof(output)) == 0);
    tlv_message_free(&message);
}

int main(void)
{
    test_known_message_is_canonical();
    test_unknown_records_are_preserved();
    test_malformed_lengths_are_rejected();
    test_parse_errors_are_transactional();
    test_all_known_duplicates_are_rejected();
    test_parse_replaces_previous_contents();
    test_short_output_is_not_modified();

    if (failures != 0) {
        fprintf(stderr, "%d test check(s) failed\n", failures);
        return 1;
    }
    puts("all TLV tests passed");
    return 0;
}
