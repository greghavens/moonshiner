#ifndef TELEMETRY_H
#define TELEMETRY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TELEMETRY_SYNC_BYTE UINT8_C(0xA5)
#define TELEMETRY_CHECKSUM_INPUT_SIZE 9u
#define TELEMETRY_FRAME_SIZE 11u

/* Application-side values. This type is deliberately not a wire format. */
typedef struct {
    uint8_t type;
    uint32_t sequence;
    uint16_t millivolts;
    uint8_t status;
} telemetry_sample;

/* Deployed v1 wire format. Multi-byte fields are byte arrays by design. */
typedef struct __attribute__((__packed__)) {
    uint8_t sync;
    uint8_t type;
    uint8_t sequence_be[4];
    uint8_t millivolts_le[2];
    uint8_t status;
    uint8_t checksum_le[2];
} telemetry_frame;

_Static_assert(sizeof(telemetry_frame) == TELEMETRY_FRAME_SIZE,
               "telemetry v1 frame size changed");
_Static_assert(offsetof(telemetry_frame, sync) == 0u,
               "telemetry v1 sync offset changed");
_Static_assert(offsetof(telemetry_frame, type) == 1u,
               "telemetry v1 type offset changed");
_Static_assert(offsetof(telemetry_frame, sequence_be) == 2u,
               "telemetry v1 sequence offset changed");
_Static_assert(offsetof(telemetry_frame, millivolts_le) == 6u,
               "telemetry v1 millivolts offset changed");
_Static_assert(offsetof(telemetry_frame, status) == 8u,
               "telemetry v1 status offset changed");
_Static_assert(offsetof(telemetry_frame, checksum_le) == 9u,
               "telemetry v1 checksum offset changed");

int telemetry_encode(const telemetry_sample *sample, telemetry_frame *out);
bool telemetry_frame_checksum_valid(const telemetry_frame *frame);

#endif
