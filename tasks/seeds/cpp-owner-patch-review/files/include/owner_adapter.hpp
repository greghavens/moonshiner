#ifndef MOONSHINER_OWNER_ADAPTER_HPP
#define MOONSHINER_OWNER_ADAPTER_HPP

#include "owner_api.h"

namespace owner_adapter {

using Handle = owner_session;
using Resource = owner_resource;
using Operations = owner_resource_ops;
using Status = owner_status;

inline Status create(const Operations& operations, Handle** out) {
    return owner_session_create(&operations, out);
}

inline void destroy(Handle* handle) {
    owner_session_destroy(handle);
}

inline Resource device(const Handle* handle) {
    return owner_session_device(handle);
}

inline Resource channel(const Handle* handle) {
    return owner_session_channel(handle);
}

}  // namespace owner_adapter

#endif
