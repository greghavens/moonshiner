#include "session.h"

/* This translation unit is expected NOT to compile. A public caller must not
 * be able to name or mutate any part of the session representation. */
int session_representation_leaked(session *value) {
    value->phase = SESSION_CLOSED;
    value->rx_bytes++;
    value->tx_bytes++;
    return (int)value->id + (value->peer != NULL) +
           (value->allocator.context != NULL);
}
