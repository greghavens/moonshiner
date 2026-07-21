#include "session_adapter.h"

#include <stddef.h>
#include <string.h>

static void write_le32(uint8_t out[4], uint32_t value) {
    for (size_t i = 0; i < 4; i++)
        out[i] = (uint8_t)(value >> (i * 8u));
}

static void write_le64(uint8_t out[8], uint64_t value) {
    for (size_t i = 0; i < 8; i++)
        out[i] = (uint8_t)(value >> (i * 8u));
}

int session_adapter_snapshot(const session *value, adapter_session_v1 *out) {
    if (value == NULL || out == NULL)
        return -1;

    adapter_session_v1 candidate;
    memset(&candidate, 0, sizeof candidate);
    candidate.version = ADAPTER_SESSION_V1_VERSION;
    candidate.phase = (uint8_t)value->phase;
    write_le32(candidate.session_id_le, value->id);
    write_le64(candidate.rx_bytes_le, value->rx_bytes);
    write_le64(candidate.tx_bytes_le, value->tx_bytes);

    size_t peer_length = strlen(value->peer);
    if (peer_length > sizeof candidate.peer)
        peer_length = sizeof candidate.peer;
    candidate.peer_length = (uint8_t)peer_length;
    memcpy(candidate.peer, value->peer, peer_length);

    *out = candidate;
    return 0;
}
