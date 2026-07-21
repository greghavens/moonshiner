#include "tlv.h"

#include <stdlib.h>
#include <string.h>

static uint16_t read_u16_be(const uint8_t *bytes)
{
    return (uint16_t)(((uint16_t)bytes[0] << 8) | bytes[1]);
}

static uint32_t read_u32_be(const uint8_t *bytes)
{
    return ((uint32_t)bytes[0] << 24) |
           ((uint32_t)bytes[1] << 16) |
           ((uint32_t)bytes[2] << 8) |
           (uint32_t)bytes[3];
}

static void write_u16_be(uint8_t *output, uint16_t value)
{
    output[0] = (uint8_t)(value >> 8);
    output[1] = (uint8_t)value;
}

static void write_u32_be(uint8_t *output, uint32_t value)
{
    output[0] = (uint8_t)(value >> 24);
    output[1] = (uint8_t)(value >> 16);
    output[2] = (uint8_t)(value >> 8);
    output[3] = (uint8_t)value;
}

void tlv_message_init(struct tlv_message *message)
{
    if (message != NULL) {
        memset(message, 0, sizeof(*message));
    }
}

void tlv_message_free(struct tlv_message *message)
{
    if (message != NULL) {
        free(message->unknown_fields);
        memset(message, 0, sizeof(*message));
    }
}

int tlv_parse(const uint8_t *wire, size_t wire_len, struct tlv_message *message)
{
    size_t offset = 0;

    if (message == NULL || (wire == NULL && wire_len != 0)) {
        return TLV_ERR_ARGUMENT;
    }

    tlv_message_free(message);

    /* Legacy v1 accepted only the tags it understood. */
    while (offset + 3u <= wire_len) {
        uint8_t tag = wire[offset];
        uint16_t value_len = read_u16_be(wire + offset + 1u);
        const uint8_t *value;

        offset += 3u;
        if ((size_t)value_len > wire_len - offset) {
            return TLV_ERR_TRUNCATED;
        }
        value = wire + offset;

        switch (tag) {
        case TLV_TAG_KIND:
            if (value_len != 1u) {
                return TLV_ERR_LENGTH;
            }
            message->has_kind = true;
            message->kind = value[0];
            break;
        case TLV_TAG_SEQUENCE:
            if (value_len != 4u) {
                return TLV_ERR_LENGTH;
            }
            message->has_sequence = true;
            message->sequence = read_u32_be(value);
            break;
        case TLV_TAG_FLAGS:
            if (value_len != 2u) {
                return TLV_ERR_LENGTH;
            }
            message->has_flags = true;
            message->flags = read_u16_be(value);
            break;
        case TLV_TAG_BODY:
            if (value_len > TLV_MAX_BODY) {
                return TLV_ERR_LENGTH;
            }
            message->has_body = true;
            message->body_len = value_len;
            memcpy(message->body, value, value_len);
            break;
        default:
            return TLV_ERR_LENGTH;
        }

        offset += value_len;
    }

    return TLV_OK;
}

static void emit_header(uint8_t *output, size_t *offset,
                        uint8_t tag, uint16_t value_len)
{
    output[*offset] = tag;
    write_u16_be(output + *offset + 1u, value_len);
    *offset += 3u;
}

int tlv_serialize(const struct tlv_message *message,
                  uint8_t *output,
                  size_t capacity,
                  size_t *written)
{
    size_t needed = 0;
    size_t offset = 0;

    if (message == NULL || written == NULL) {
        return TLV_ERR_ARGUMENT;
    }
    if (message->has_kind) {
        needed += 4u;
    }
    if (message->has_sequence) {
        needed += 7u;
    }
    if (message->has_flags) {
        needed += 5u;
    }
    if (message->has_body) {
        if (message->body_len > TLV_MAX_BODY) {
            return TLV_ERR_LENGTH;
        }
        needed += 3u + message->body_len;
    }
    *written = needed;
    if (needed > capacity || (needed != 0u && output == NULL)) {
        return TLV_ERR_SPACE;
    }

    if (message->has_kind) {
        emit_header(output, &offset, TLV_TAG_KIND, 1u);
        output[offset++] = message->kind;
    }
    if (message->has_sequence) {
        emit_header(output, &offset, TLV_TAG_SEQUENCE, 4u);
        write_u32_be(output + offset, message->sequence);
        offset += 4u;
    }
    if (message->has_flags) {
        emit_header(output, &offset, TLV_TAG_FLAGS, 2u);
        write_u16_be(output + offset, message->flags);
        offset += 2u;
    }
    if (message->has_body) {
        emit_header(output, &offset, TLV_TAG_BODY, message->body_len);
        memcpy(output + offset, message->body, message->body_len);
        offset += message->body_len;
    }
    *written = offset;
    return TLV_OK;
}
