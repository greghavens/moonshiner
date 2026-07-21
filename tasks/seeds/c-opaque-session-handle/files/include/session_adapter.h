#ifndef SESSION_ADAPTER_H
#define SESSION_ADAPTER_H

#include "adapter/session_wire.h"
#include "session.h"

/* Write one deployed v1 record. On invalid arguments, out is untouched. */
int session_adapter_snapshot(const session *value, adapter_session_v1 *out);

#endif /* SESSION_ADAPTER_H */
