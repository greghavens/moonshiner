#include "session_pump.h"

#include <stddef.h>

int session_pump_transfer(session *value, uint32_t received,
                          uint32_t transmitted) {
    if (value == NULL || value->phase != SESSION_ESTABLISHED)
        return -1;
    value->rx_bytes += received;
    value->tx_bytes += transmitted;
    return 0;
}

void session_pump_close(session *value) {
    if (value != NULL)
        value->phase = SESSION_CLOSED;
}
