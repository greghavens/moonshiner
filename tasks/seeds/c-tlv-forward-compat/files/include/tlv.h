#ifndef TLV_H
#define TLV_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TLV_MAX_BODY 1024u

enum tlv_status {
    TLV_OK = 0,
    TLV_ERR_ARGUMENT = -1,
    TLV_ERR_TRUNCATED = -2,
    TLV_ERR_LENGTH = -3,
    TLV_ERR_DUPLICATE = -4,
    TLV_ERR_NO_MEMORY = -5,
    TLV_ERR_SPACE = -6
};

enum tlv_tag {
    TLV_TAG_KIND = 0x01,
    TLV_TAG_SEQUENCE = 0x02,
    TLV_TAG_FLAGS = 0x03,
    TLV_TAG_BODY = 0x04
};

/*
 * Every wire record is:
 *
 *     tag:u8 | value_length:u16 (big endian) | value:value_length bytes
 *
 * KIND, SEQUENCE, FLAGS, and BODY have lengths 1, 4, 2, and 0..TLV_MAX_BODY
 * respectively. Each known tag may occur at most once; a second occurrence is
 * TLV_ERR_DUPLICATE. Unknown tags, including repeated unknown tags, are valid.
 * Their complete encoded records are retained in encounter order in
 * unknown_fields so a decode/encode bridge does not discard future fields.
 *
 * The encoder emits present known fields in numeric tag order, followed by the
 * retained unknown records in their original order. Consequently a known-only
 * message always has one canonical, byte-stable encoding.
 */
struct tlv_message {
    bool has_kind;
    uint8_t kind;

    bool has_sequence;
    uint32_t sequence;

    bool has_flags;
    uint16_t flags;

    bool has_body;
    uint16_t body_len;
    uint8_t body[TLV_MAX_BODY];

    uint8_t *unknown_fields;
    size_t unknown_fields_len;
};

void tlv_message_init(struct tlv_message *message);
void tlv_message_free(struct tlv_message *message);

/*
 * message must have been initialized before parsing. Parsing is transactional:
 * on any error, message is left initialized and empty. A trailing partial
 * header, a value extending past wire_len, or an invalid known-field length is
 * an error rather than data to ignore.
 */
int tlv_parse(const uint8_t *wire, size_t wire_len, struct tlv_message *message);

/*
 * On success, *written is the encoded size. If capacity is too small,
 * TLV_ERR_SPACE is returned, *written receives the required size, and output is
 * not changed.
 */
int tlv_serialize(const struct tlv_message *message,
                  uint8_t *output,
                  size_t capacity,
                  size_t *written);

#endif
