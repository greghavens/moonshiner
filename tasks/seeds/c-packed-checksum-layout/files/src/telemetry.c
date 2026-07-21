#include "telemetry.h"

#include <stddef.h>

static uint16_t crc16_ccitt_false(const uint8_t *bytes, size_t length) {
    uint16_t crc = UINT16_C(0xFFFF);

    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)bytes[i] << 8;
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc & UINT16_C(0x8000)) != 0
                      ? (uint16_t)((crc << 1) ^ UINT16_C(0x1021))
                      : (uint16_t)(crc << 1);
        }
    }

    return crc;
}

static void store_be32(uint8_t out[4], uint32_t value) {
    out[0] = (uint8_t)(value >> 24);
    out[1] = (uint8_t)(value >> 16);
    out[2] = (uint8_t)(value >> 8);
    out[3] = (uint8_t)value;
}

static void store_le16(uint8_t out[2], uint16_t value) {
    out[0] = (uint8_t)value;
    out[1] = (uint8_t)(value >> 8);
}

static uint16_t load_le16(const uint8_t in[2]) {
    return (uint16_t)((uint16_t)in[0] | ((uint16_t)in[1] << 8));
}

int telemetry_encode(const telemetry_sample *sample, telemetry_frame *out) {
    if (sample == NULL || out == NULL)
        return -1;

    out->sync = TELEMETRY_SYNC_BYTE;
    out->type = sample->type;
    store_be32(out->sequence_be, sample->sequence);
    store_le16(out->millivolts_le, sample->millivolts);
    out->status = sample->status;

    /* BUG: telemetry_sample is a host object, not the serialized protocol. */
    const uint16_t checksum =
        crc16_ccitt_false((const uint8_t *)sample, sizeof(*sample));
    store_le16(out->checksum_le, checksum);
    return 0;
}

bool telemetry_frame_checksum_valid(const telemetry_frame *frame) {
    if (frame == NULL)
        return false;

    const uint16_t actual = crc16_ccitt_false(
        (const uint8_t *)frame, TELEMETRY_CHECKSUM_INPUT_SIZE);
    return actual == load_le16(frame->checksum_le);
}
