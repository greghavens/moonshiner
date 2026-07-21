#ifndef SESSION_PUMP_H
#define SESSION_PUMP_H

#include <stdint.h>

#include "session.h"

/* Traffic is accepted only while the session is established. */
int session_pump_transfer(session *value, uint32_t received,
                          uint32_t transmitted);
void session_pump_close(session *value);

#endif /* SESSION_PUMP_H */
