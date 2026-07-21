#include "session.h"

#include <stdlib.h>
#include <string.h>

static void *default_allocate(void *context, size_t size) {
    (void)context;
    return malloc(size);
}

static void default_release(void *context, void *pointer) {
    (void)context;
    free(pointer);
}

session *session_create(const session_options *options,
                        const session_allocator *allocator) {
    if (options == NULL || options->peer == NULL)
        return NULL;

    session_allocator selected = {
        .allocate = default_allocate,
        .release = default_release,
        .context = NULL,
    };
    if (allocator != NULL) {
        if (allocator->allocate == NULL || allocator->release == NULL)
            return NULL;
        selected = *allocator;
    }

    session *value = selected.allocate(selected.context, sizeof *value);
    if (value == NULL)
        return NULL;

    size_t peer_size = strlen(options->peer) + 1u;
    char *peer = selected.allocate(selected.context, peer_size);
    if (peer == NULL) {
        selected.release(selected.context, value);
        return NULL;
    }
    memcpy(peer, options->peer, peer_size);

    value->id = options->id;
    value->phase = SESSION_CONNECTING;
    value->peer = peer;
    value->rx_bytes = 0;
    value->tx_bytes = 0;
    value->allocator = selected;
    return value;
}

void session_destroy(session *value) {
    if (value == NULL)
        return;
    session_allocator allocator = value->allocator;
    char *peer = value->peer;
    allocator.release(allocator.context, peer);
    allocator.release(allocator.context, value);
}

uint32_t session_id(const session *value) {
    return value != NULL ? value->id : 0u;
}

const char *session_peer(const session *value) {
    return value != NULL ? value->peer : NULL;
}

session_phase session_get_phase(const session *value) {
    return value != NULL ? value->phase : SESSION_CLOSED;
}

uint64_t session_rx_bytes(const session *value) {
    return value != NULL ? value->rx_bytes : 0u;
}

uint64_t session_tx_bytes(const session *value) {
    return value != NULL ? value->tx_bytes : 0u;
}

void session_set_phase(session *value, session_phase phase) {
    if (value != NULL)
        value->phase = phase;
}

void session_record_traffic(session *value, uint32_t received,
                            uint32_t transmitted) {
    if (value == NULL)
        return;
    value->rx_bytes += received;
    value->tx_bytes += transmitted;
}
