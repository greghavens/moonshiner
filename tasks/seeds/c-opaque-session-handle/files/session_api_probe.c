#include "session.h"

#define HAS_TYPE(expression, type) _Generic((expression), type: 1, default: 0)

_Static_assert(SESSION_CONNECTING == 1, "SESSION_CONNECTING value changed");
_Static_assert(SESSION_ESTABLISHED == 2, "SESSION_ESTABLISHED value changed");
_Static_assert(SESSION_CLOSED == 3, "SESSION_CLOSED value changed");

_Static_assert(HAS_TYPE(&session_create,
                        session *(*)(const session_options *,
                                     const session_allocator *)),
               "session_create signature changed");
_Static_assert(HAS_TYPE(&session_destroy, void (*)(session *)),
               "session_destroy signature changed");
_Static_assert(HAS_TYPE(&session_id, uint32_t (*)(const session *)),
               "session_id signature changed");
_Static_assert(HAS_TYPE(&session_peer, const char *(*)(const session *)),
               "session_peer signature changed");
_Static_assert(HAS_TYPE(&session_get_phase,
                        session_phase (*)(const session *)),
               "session_get_phase signature changed");
_Static_assert(HAS_TYPE(&session_rx_bytes, uint64_t (*)(const session *)),
               "session_rx_bytes signature changed");
_Static_assert(HAS_TYPE(&session_tx_bytes, uint64_t (*)(const session *)),
               "session_tx_bytes signature changed");
_Static_assert(HAS_TYPE(&session_set_phase,
                        void (*)(session *, session_phase)),
               "session_set_phase signature changed");
_Static_assert(HAS_TYPE(&session_record_traffic,
                        void (*)(session *, uint32_t, uint32_t)),
               "session_record_traffic signature changed");

session *session_alias_accepts_tag(struct session *value) {
    return value;
}

struct session *session_tag_accepts_alias(session *value) {
    return value;
}
