#include "packed_record.h"

#include <limits.h>
#include <stdbool.h>
#include <string.h>

enum {
    V0_HEADER_SIZE = 15,
    V1_HEADER_SIZE = 20,
    V1_ALIGNMENT = 8
};

static uint16_t read_le16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] |
                      ((uint16_t)p[1] << 8));
}

static uint32_t read_le32(const uint8_t *p)
{
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static uint64_t read_le64(const uint8_t *p)
{
    uint64_t value = 0;
    unsigned int i;

    for (i = 0; i < 8; ++i) {
        value |= (uint64_t)p[i] << (i * 8);
    }
    return value;
}

static uint32_t read_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static uint64_t read_be64(const uint8_t *p)
{
    uint64_t value = 0;
    unsigned int i;

    for (i = 0; i < 8; ++i) {
        value = (value << 8) | p[i];
    }
    return value;
}

static void write_be32(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static void write_be64(uint8_t *p, uint64_t value)
{
    unsigned int i;

    for (i = 0; i < 8; ++i) {
        p[7 - i] = (uint8_t)(value >> (i * 8));
    }
}

static bool v1_wire_size(size_t payload_length, size_t *wire_size)
{
    size_t unpadded;

#if SIZE_MAX > UINT32_MAX
    if (payload_length > UINT32_MAX) {
        return false;
    }
#endif
    if (payload_length > SIZE_MAX - V1_HEADER_SIZE) {
        return false;
    }

    unpadded = V1_HEADER_SIZE + payload_length;
    if (unpadded > SIZE_MAX - (V1_ALIGNMENT - 1)) {
        return false;
    }

    *wire_size = (unpadded + (V1_ALIGNMENT - 1)) &
                 ~(size_t)(V1_ALIGNMENT - 1);
    return true;
}

static packed_status decode_v0(const uint8_t *input,
                               size_t input_length,
                               packed_record *output)
{
    uint16_t payload_length;
    size_t expected;
    packed_record decoded;

    if (input_length < V0_HEADER_SIZE) {
        return PACKED_BAD_FORMAT;
    }

    payload_length = read_le16(input + 13);
    expected = V0_HEADER_SIZE + (size_t)payload_length;
    if (input_length != expected) {
        return PACKED_BAD_FORMAT;
    }

    decoded.record_id = read_le32(input + 1);
    decoded.timestamp = read_le64(input + 5);
    decoded.payload = input + V0_HEADER_SIZE;
    decoded.payload_length = payload_length;
    *output = decoded;
    return PACKED_OK;
}

static packed_status decode_v1(const uint8_t *input,
                               size_t input_length,
                               packed_record *output)
{
    uint32_t payload_length;
    size_t unpadded;
    size_t expected;
    size_t i;
    packed_record decoded;

    if (input_length < V1_HEADER_SIZE) {
        return PACKED_BAD_FORMAT;
    }
    if (input[1] != 0 || input[2] != 0 || input[3] != 0) {
        return PACKED_BAD_FORMAT;
    }

    payload_length = read_be32(input + 16);
#if SIZE_MAX <= UINT32_MAX
    if (payload_length > SIZE_MAX - V1_HEADER_SIZE) {
        return PACKED_BAD_FORMAT;
    }
#endif
    unpadded = V1_HEADER_SIZE + (size_t)payload_length;
    if (unpadded > SIZE_MAX - (V1_ALIGNMENT - 1)) {
        return PACKED_BAD_FORMAT;
    }

    /* Validate the complete wire size before exposing payload bytes. */
    expected = unpadded;
    if (input_length != expected) {
        return PACKED_BAD_FORMAT;
    }
    for (i = unpadded; i < expected; ++i) {
        if (input[i] != 0) {
            return PACKED_BAD_FORMAT;
        }
    }

    decoded.record_id = read_be32(input + 4);
    decoded.timestamp = read_be64(input + 8);
    decoded.payload = input + V1_HEADER_SIZE;
    decoded.payload_length = payload_length;
    *output = decoded;
    return PACKED_OK;
}

packed_status packed_record_decode(const uint8_t *input,
                                   size_t input_length,
                                   packed_record *output)
{
    if (input == NULL || output == NULL) {
        return PACKED_INVALID_ARGUMENT;
    }
    if (input_length == 0) {
        return PACKED_BAD_FORMAT;
    }

    if (input[0] == 0) {
        return decode_v0(input, input_length, output);
    }
    if (input[0] == 1) {
        return decode_v1(input, input_length, output);
    }
    return PACKED_BAD_FORMAT;
}

packed_status packed_record_encode(const packed_record *record,
                                   uint8_t *output,
                                   size_t output_capacity,
                                   size_t *written)
{
    size_t needed;

    if (record == NULL || written == NULL ||
        (record->payload_length != 0 && record->payload == NULL)) {
        return PACKED_INVALID_ARGUMENT;
    }
    if (!v1_wire_size(record->payload_length, &needed)) {
        return PACKED_RANGE;
    }

    *written = needed;
    if (output_capacity < needed) {
        return PACKED_NO_SPACE;
    }
    if (output == NULL) {
        return PACKED_INVALID_ARGUMENT;
    }

    memset(output, 0, needed);
    output[0] = 1;
    write_be32(output + 4, record->record_id);
    write_be64(output + 8, record->timestamp);
    write_be32(output + 16, (uint32_t)record->payload_length);
    if (record->payload_length != 0) {
        memcpy(output + V1_HEADER_SIZE,
               record->payload,
               record->payload_length);
    }
    return PACKED_OK;
}
