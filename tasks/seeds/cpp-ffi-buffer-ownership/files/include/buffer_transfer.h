#ifndef BUFFER_TRANSFER_H
#define BUFFER_TRANSFER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*ffi_buffer_release_fn)(void *owner,
                                      uint8_t *data,
                                      size_t size);

struct ffi_owned_buffer {
    uint8_t *data;
    size_t size;
    void *owner;
    ffi_buffer_release_fn release;
};

enum ffi_completion_status {
    FFI_COMPLETION_SUCCESS = 0,
    FFI_COMPLETION_FAILED = 1,
    FFI_COMPLETION_CANCELLED = 2
};

typedef void (*ffi_completion_fn)(void *callback_context,
                                  enum ffi_completion_status status);

enum ffi_submit_disposition {
    FFI_SUBMIT_REJECTED = 0,
    FFI_SUBMIT_ACCEPTED = 1
};

enum ffi_cancel_disposition {
    /* The transport has dropped the buffer loan and will not call back. */
    FFI_CANCEL_CALLBACK_SUPPRESSED = 0,

    /*
     * The transport retains the loan and promises exactly one later callback.
     * It may continue reading the buffer until that callback begins.
     */
    FFI_CANCEL_CALLBACK_PENDING = 1
};

struct ffi_sender {
    void *context;
    enum ffi_submit_disposition (*submit)(
        void *context,
        const uint8_t *data,
        size_t size,
        ffi_completion_fn completion,
        void *completion_context,
        uint64_t *request_id);
    enum ffi_cancel_disposition (*cancel)(void *context,
                                          uint64_t request_id);
};

#ifdef __cplusplus
} /* extern "C" */

namespace buffer_bridge {

class pending_send;

enum class submit_status {
    accepted,
    rejected,
    no_memory
};

struct submit_result {
    pending_send *pending;
    submit_status status;
};

/*
 * Consumes buffer ownership on every return path. A transport callback may
 * occur only after submit returns FFI_SUBMIT_ACCEPTED, and submit, cancel, and
 * completion callbacks are serialized by the caller.
 */
submit_result submit(const struct ffi_sender *sender,
                     struct ffi_owned_buffer buffer) noexcept;

/* Consumes the pending handle. */
void cancel(pending_send *pending) noexcept;

} /* namespace buffer_bridge */
#endif

#endif
