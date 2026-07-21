#ifndef SESSION_H
#define SESSION_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
    SESSION_CONNECTING = 1,
    SESSION_ESTABLISHED = 2,
    SESSION_CLOSED = 3
} session_phase;

typedef void *(*session_allocate_fn)(void *context, size_t size);
typedef void (*session_release_fn)(void *context, void *pointer);

typedef struct {
    session_allocate_fn allocate;
    session_release_fn release;
    void *context;
} session_allocator;

typedef struct {
    uint32_t id;
    const char *peer;
} session_options;

/* TODO: this representation escaped during the original adapter integration. */
typedef struct session {
    uint32_t id;
    session_phase phase;
    char *peer;
    uint64_t rx_bytes;
    uint64_t tx_bytes;
    session_allocator allocator;
} session;

session *session_create(const session_options *options,
                        const session_allocator *allocator);
void session_destroy(session *value);

uint32_t session_id(const session *value);
const char *session_peer(const session *value);
session_phase session_get_phase(const session *value);
uint64_t session_rx_bytes(const session *value);
uint64_t session_tx_bytes(const session *value);

void session_set_phase(session *value, session_phase phase);
void session_record_traffic(session *value, uint32_t received,
                            uint32_t transmitted);

#endif /* SESSION_H */
