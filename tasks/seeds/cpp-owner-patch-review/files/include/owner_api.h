#ifndef MOONSHINER_OWNER_API_H
#define MOONSHINER_OWNER_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void* owner_resource;
typedef struct owner_session owner_session;

typedef enum owner_status {
    OWNER_OK = 0,
    OWNER_INVALID_ARGUMENT = 1,
    OWNER_DEVICE_ERROR = 2,
    OWNER_CHANNEL_ERROR = 3,
    OWNER_CONFIGURE_ERROR = 4,
    OWNER_INTERNAL_ERROR = 5
} owner_status;

typedef struct owner_resource_ops {
    uint32_t struct_size;
    void* context;
    owner_resource (*create_device)(void* context);
    void (*destroy_device)(void* context, owner_resource device);
    owner_resource (*create_channel)(void* context, owner_resource device);
    void (*destroy_channel)(void* context, owner_resource channel);
    int (*configure)(void* context, owner_resource device, owner_resource channel);
} owner_resource_ops;

owner_status owner_session_create(const owner_resource_ops* ops, owner_session** out);
void owner_session_destroy(owner_session* session);
owner_resource owner_session_device(const owner_session* session);
owner_resource owner_session_channel(const owner_session* session);

#ifdef __cplusplus
}
#endif

#endif
