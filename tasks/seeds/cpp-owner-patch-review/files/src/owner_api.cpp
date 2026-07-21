#include "owner_api.h"

#include <cstddef>
#include <exception>
#include <memory>
#include <new>
#include <utility>

namespace {

class BuildFailure final : public std::exception {
public:
    explicit BuildFailure(owner_status status) noexcept : status_(status) {}

    owner_status status() const noexcept {
        return status_;
    }

private:
    owner_status status_;
};

struct CallbackDeleter {
    void* context;
    void (*destroy)(void*, owner_resource);

    void operator()(owner_resource resource) const noexcept {
        if (resource != nullptr && destroy != nullptr) {
            destroy(context, resource);
        }
    }
};

using ResourceOwner = std::unique_ptr<void, CallbackDeleter>;

bool valid_operations(const owner_resource_ops& ops) noexcept {
    return ops.struct_size == sizeof(owner_resource_ops) &&
           ops.create_device != nullptr &&
           ops.destroy_device != nullptr &&
           ops.create_channel != nullptr &&
           ops.destroy_channel != nullptr &&
           ops.configure != nullptr;
}

}  // namespace

struct owner_session final {
    explicit owner_session(const owner_resource_ops& operations)
        : operations_(operations),
          device_(nullptr, CallbackDeleter{operations.context, operations.destroy_device}),
          device_view_(nullptr),
          channel_(nullptr, CallbackDeleter{operations.context, operations.destroy_channel}),
          channel_view_(nullptr) {
        try {
            build();
        } catch (...) {
            // Kept from the old raw-owner implementation.
            if (channel_view_ != nullptr) {
                operations_.destroy_channel(operations_.context, channel_view_);
            }
            if (device_view_ != nullptr) {
                operations_.destroy_device(operations_.context, device_view_);
            }
            throw;
        }
    }

    owner_resource device_view() const noexcept {
        return device_view_;
    }

    owner_resource channel_view() const noexcept {
        return channel_view_;
    }

private:
    void build() {
        device_.reset(operations_.create_device(operations_.context));
        device_view_ = device_.get();
        if (device_view_ == nullptr) {
            throw BuildFailure(OWNER_DEVICE_ERROR);
        }

        channel_.reset(
            operations_.create_channel(operations_.context, device_view_));
        channel_view_ = channel_.get();
        if (channel_view_ == nullptr) {
            throw BuildFailure(OWNER_CHANNEL_ERROR);
        }

        if (operations_.configure(
                operations_.context, device_view_, channel_view_) == 0) {
            throw BuildFailure(OWNER_CONFIGURE_ERROR);
        }
    }

    owner_resource_ops operations_;
    ResourceOwner device_;
    owner_resource device_view_;   // Borrowed ABI view; device_ is the owner.
    ResourceOwner channel_;
    owner_resource channel_view_;  // Borrowed ABI view; channel_ is the owner.
};

extern "C" owner_status owner_session_create(
    const owner_resource_ops* operations, owner_session** out) {
    if (out == nullptr) {
        return OWNER_INVALID_ARGUMENT;
    }
    *out = nullptr;
    if (operations == nullptr || !valid_operations(*operations)) {
        return OWNER_INVALID_ARGUMENT;
    }

    try {
        *out = new owner_session(*operations);
        return OWNER_OK;
    } catch (const BuildFailure& failure) {
        return failure.status();
    } catch (...) {
        return OWNER_INTERNAL_ERROR;
    }
}

extern "C" void owner_session_destroy(owner_session* session) {
    delete session;
}

extern "C" owner_resource owner_session_device(const owner_session* session) {
    return session == nullptr ? nullptr : session->device_view();
}

extern "C" owner_resource owner_session_channel(const owner_session* session) {
    return session == nullptr ? nullptr : session->channel_view();
}
