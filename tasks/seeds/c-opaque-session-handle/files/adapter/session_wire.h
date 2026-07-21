/* Deployed relay-adapter v1 record. Consumers persist these exact bytes. */
#ifndef SESSION_WIRE_H
#define SESSION_WIRE_H

#include <stdint.h>

#if defined(__GNUC__)
#define SESSION_WIRE_PACKED __attribute__((packed))
#else
#define SESSION_WIRE_PACKED
#endif

typedef struct SESSION_WIRE_PACKED {
    uint8_t version;
    uint8_t phase;
    uint8_t peer_length;
    uint8_t reserved;
    uint8_t session_id_le[4];
    uint8_t rx_bytes_le[8];
    uint8_t tx_bytes_le[8];
    char peer[16];
} adapter_session_v1;

#define ADAPTER_SESSION_V1_VERSION 1u

#endif /* SESSION_WIRE_H */
