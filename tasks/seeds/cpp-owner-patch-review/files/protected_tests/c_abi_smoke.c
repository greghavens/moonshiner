#include "owner_api.h"

#include <stddef.h>

static owner_resource create_device(void* context) {
    (void)context;
    return NULL;
}

static void destroy_device(void* context, owner_resource device) {
    (void)context;
    (void)device;
}

static owner_resource create_channel(void* context, owner_resource device) {
    (void)context;
    (void)device;
    return NULL;
}

static void destroy_channel(void* context, owner_resource channel) {
    (void)context;
    (void)channel;
}

static int configure(
    void* context, owner_resource device, owner_resource channel) {
    (void)context;
    (void)device;
    (void)channel;
    return 1;
}

int main(void) {
    owner_resource_ops operations = {
        (uint32_t)sizeof(owner_resource_ops),
        NULL,
        create_device,
        destroy_device,
        create_channel,
        destroy_channel,
        configure
    };
    owner_session* session = (owner_session*)1;

    if (OWNER_OK != 0 || OWNER_INTERNAL_ERROR != 5) {
        return 1;
    }
    if (owner_session_create(&operations, &session) != OWNER_DEVICE_ERROR) {
        return 2;
    }
    if (session != NULL) {
        return 3;
    }
    if (owner_session_device(NULL) != NULL ||
        owner_session_channel(NULL) != NULL) {
        return 4;
    }
    owner_session_destroy(NULL);
    return 0;
}
