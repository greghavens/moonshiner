#include "buffer_transfer.h"

#include <new>

namespace buffer_bridge {

class pending_send final {
  public:
    struct ffi_sender sender;
    struct ffi_owned_buffer buffer;
    uint64_t request_id;
};

namespace {

void release_owned_buffer(struct ffi_owned_buffer &buffer) noexcept {
    if (buffer.release != nullptr) {
        buffer.release(buffer.owner, buffer.data, buffer.size);
    }
}

void completion_trampoline(void *context,
                           enum ffi_completion_status status) {
    (void)status;
    auto *const pending = static_cast<pending_send *>(context);
    release_owned_buffer(pending->buffer);
    delete pending;
}

} /* namespace */

submit_result submit(const struct ffi_sender *sender,
                     struct ffi_owned_buffer buffer) noexcept {
    if (sender == nullptr || sender->submit == nullptr ||
        sender->cancel == nullptr || buffer.release == nullptr) {
        release_owned_buffer(buffer);
        return {nullptr, submit_status::rejected};
    }

    auto *const pending =
        new (std::nothrow) pending_send{*sender, buffer, UINT64_C(0)};
    if (pending == nullptr) {
        release_owned_buffer(buffer);
        return {nullptr, submit_status::no_memory};
    }

    const enum ffi_submit_disposition disposition = sender->submit(
        sender->context,
        buffer.data,
        buffer.size,
        completion_trampoline,
        pending,
        &pending->request_id);

    if (disposition != FFI_SUBMIT_ACCEPTED) {
        release_owned_buffer(pending->buffer);
        delete pending;
        return {nullptr, submit_status::rejected};
    }

    return {pending, submit_status::accepted};
}

void cancel(pending_send *pending) noexcept {
    if (pending == nullptr) {
        return;
    }

    const enum ffi_cancel_disposition disposition =
        pending->sender.cancel(pending->sender.context, pending->request_id);

    release_owned_buffer(pending->buffer);
    if (disposition == FFI_CANCEL_CALLBACK_SUPPRESSED) {
        delete pending;
    }
}

} /* namespace buffer_bridge */
