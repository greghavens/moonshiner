#include "buffer_transfer.h"

#include <cstddef>
#include <cstdint>
#include <iostream>

namespace {

int failures = 0;

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            std::cerr << "FAIL line " << __LINE__ << ": " #condition << '\n'; \
            ++failures;                                                        \
        }                                                                      \
    } while (false)

struct release_audit {
    uint8_t *expected_data = nullptr;
    std::size_t expected_size = 0U;
    int calls = 0;
    bool tuple_mismatch = false;
};

extern "C" void audited_release(void *owner,
                                uint8_t *data,
                                std::size_t size) {
    auto *const audit = static_cast<release_audit *>(owner);
    ++audit->calls;
    if (data != audit->expected_data || size != audit->expected_size) {
        audit->tuple_mismatch = true;
    }
}

struct fake_transport {
    enum ffi_submit_disposition submit_disposition = FFI_SUBMIT_ACCEPTED;
    enum ffi_cancel_disposition cancel_disposition =
        FFI_CANCEL_CALLBACK_SUPPRESSED;
    ffi_completion_fn completion = nullptr;
    void *completion_context = nullptr;
    const uint8_t *observed_data = nullptr;
    std::size_t observed_size = 0U;
    uint64_t assigned_request_id = UINT64_C(0x1020304050607080);
    uint64_t cancelled_request_id = 0U;
    int submit_calls = 0;
    int cancel_calls = 0;

    static enum ffi_submit_disposition submit_callback(
        void *context,
        const uint8_t *data,
        std::size_t size,
        ffi_completion_fn callback,
        void *callback_context,
        uint64_t *request_id) {
        auto *const self = static_cast<fake_transport *>(context);
        ++self->submit_calls;
        self->observed_data = data;
        self->observed_size = size;
        *request_id = self->assigned_request_id;
        if (self->submit_disposition == FFI_SUBMIT_ACCEPTED) {
            self->completion = callback;
            self->completion_context = callback_context;
        }
        return self->submit_disposition;
    }

    static enum ffi_cancel_disposition cancel_callback(void *context,
                                                        uint64_t request_id) {
        auto *const self = static_cast<fake_transport *>(context);
        ++self->cancel_calls;
        self->cancelled_request_id = request_id;
        if (self->cancel_disposition == FFI_CANCEL_CALLBACK_SUPPRESSED) {
            self->completion = nullptr;
            self->completion_context = nullptr;
        }
        return self->cancel_disposition;
    }

    struct ffi_sender sender() {
        return {this, submit_callback, cancel_callback};
    }

    void complete(enum ffi_completion_status status) {
        ffi_completion_fn const callback = completion;
        void *const context = completion_context;
        completion = nullptr;
        completion_context = nullptr;
        CHECK(callback != nullptr);
        if (callback != nullptr) {
            callback(context, status);
        }
    }
};

template <std::size_t Size>
struct ffi_owned_buffer owned(uint8_t (&storage)[Size],
                              release_audit &audit) {
    audit.expected_data = storage;
    audit.expected_size = Size;
    return {storage, Size, &audit, audited_release};
}

void test_rejection_releases_immediately() {
    uint8_t storage[] = {1U, 2U, 3U};
    release_audit audit;
    fake_transport transport;
    transport.submit_disposition = FFI_SUBMIT_REJECTED;
    struct ffi_sender sender = transport.sender();

    const buffer_bridge::submit_result result =
        buffer_bridge::submit(&sender, owned(storage, audit));

    CHECK(result.status == buffer_bridge::submit_status::rejected);
    CHECK(result.pending == nullptr);
    CHECK(transport.submit_calls == 1);
    CHECK(transport.completion == nullptr);
    CHECK(audit.calls == 1);
    CHECK(!audit.tuple_mismatch);
}

void test_success_releases_at_completion() {
    uint8_t storage[] = {4U, 5U, 6U, 7U};
    release_audit audit;
    fake_transport transport;
    struct ffi_sender sender = transport.sender();

    const buffer_bridge::submit_result result =
        buffer_bridge::submit(&sender, owned(storage, audit));

    CHECK(result.status == buffer_bridge::submit_status::accepted);
    CHECK(result.pending != nullptr);
    CHECK(audit.calls == 0);
    CHECK(transport.observed_data == storage);
    CHECK(transport.observed_size == sizeof(storage));
    CHECK(transport.observed_data[2] == 6U);

    transport.complete(FFI_COMPLETION_SUCCESS);
    CHECK(audit.calls == 1);
    CHECK(!audit.tuple_mismatch);
}

void test_suppressed_cancellation_releases_during_cancel() {
    uint8_t storage[] = {8U, 9U};
    release_audit audit;
    fake_transport transport;
    transport.cancel_disposition = FFI_CANCEL_CALLBACK_SUPPRESSED;
    struct ffi_sender sender = transport.sender();

    const buffer_bridge::submit_result result =
        buffer_bridge::submit(&sender, owned(storage, audit));
    CHECK(result.status == buffer_bridge::submit_status::accepted);
    CHECK(audit.calls == 0);

    buffer_bridge::cancel(result.pending);
    CHECK(transport.cancel_calls == 1);
    CHECK(transport.cancelled_request_id == transport.assigned_request_id);
    CHECK(transport.completion == nullptr);
    CHECK(audit.calls == 1);
    CHECK(!audit.tuple_mismatch);
}

void test_pending_cancellation_retains_until_late_callback() {
    uint8_t storage[] = {10U, 11U, 12U};
    release_audit audit;
    fake_transport transport;
    transport.cancel_disposition = FFI_CANCEL_CALLBACK_PENDING;
    struct ffi_sender sender = transport.sender();

    const buffer_bridge::submit_result result =
        buffer_bridge::submit(&sender, owned(storage, audit));
    CHECK(result.status == buffer_bridge::submit_status::accepted);
    CHECK(audit.calls == 0);

    buffer_bridge::cancel(result.pending);
    CHECK(transport.cancel_calls == 1);
    CHECK(transport.completion != nullptr);
    CHECK(audit.calls == 0);
    CHECK(transport.observed_data[0] == 10U);
    CHECK(transport.observed_data[2] == 12U);

    transport.complete(FFI_COMPLETION_CANCELLED);
    CHECK(audit.calls == 1);
    CHECK(!audit.tuple_mismatch);
}

} /* namespace */

int main() {
    test_rejection_releases_immediately();
    test_success_releases_at_completion();
    test_suppressed_cancellation_releases_during_cancel();
    test_pending_cancellation_retains_until_late_callback();

    if (failures != 0) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }

    std::cout << "all buffer ownership checks passed\n";
    return 0;
}
