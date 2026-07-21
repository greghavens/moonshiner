#ifndef PACKED_RECORD_H
#define PACKED_RECORD_H

#include <stddef.h>
#include <stdint.h>

/*
 * Wire formats
 * ------------
 *
 * Legacy v0 (all integer fields are little-endian):
 *
 *   offset  size  field
 *      0      1   version (0)
 *      1      4   record_id
 *      5      8   timestamp
 *     13      2   payload_length
 *     15      n   payload
 *
 * The v0 wire size is exactly 15 + payload_length bytes. It deliberately has
 * no implicit padding; in particular, its integer fields are not naturally
 * aligned in the byte stream.
 *
 * Current v1 (all integer fields are big-endian):
 *
 *   offset  size  field
 *      0      1   version (1)
 *      1      3   reserved zero bytes
 *      4      4   record_id
 *      8      8   timestamp
 *     16      4   payload_length
 *     20      n   payload
 *   20 + n    p   zero padding to an 8-byte total wire-size boundary
 *
 * A v1 record always includes its trailing alignment padding. Decoding is
 * strict: missing or extra bytes, non-zero reserved/padding bytes, unknown
 * versions, and lengths that cannot be represented are malformed.
 */

typedef enum packed_status {
    PACKED_OK = 0,
    PACKED_INVALID_ARGUMENT,
    PACKED_BAD_FORMAT,
    PACKED_NO_SPACE,
    PACKED_RANGE
} packed_status;

typedef struct packed_record {
    uint32_t record_id;
    uint64_t timestamp;
    const uint8_t *payload;
    size_t payload_length;
} packed_record;

/*
 * Decode a complete v0 or v1 record. On success, payload points into input and
 * remains valid only as long as input does. The output is unchanged on error.
 */
packed_status packed_record_decode(const uint8_t *input,
                                   size_t input_length,
                                   packed_record *output);

/*
 * Encode a record using v1. On PACKED_OK, written is the number of bytes
 * emitted. On PACKED_NO_SPACE, written is the required capacity and output is
 * unchanged. A payload length above UINT32_MAX returns PACKED_RANGE.
 */
packed_status packed_record_encode(const packed_record *record,
                                   uint8_t *output,
                                   size_t output_capacity,
                                   size_t *written);

#endif
