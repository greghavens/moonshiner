#include "mintest.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "adapter/session_wire.h"
#include "session.h"
#include "session_adapter.h"
#include "session_pump.h"

typedef union {
    max_align_t alignment;
    unsigned char bytes[256];
} allocation_block;

typedef struct {
    size_t calls;
    size_t fail_on;
    size_t issued;
    size_t unexpected_release_count;
    size_t allocated_count;
    void *allocated[4];
    size_t released_count;
    void *released[4];
    allocation_block blocks[4];
} allocator_probe;

static void *probe_allocate(void *context, size_t size) {
    allocator_probe *probe = context;
    probe->calls++;
    if (probe->fail_on != 0 && probe->calls == probe->fail_on)
        return NULL;
    if (size > sizeof probe->blocks[0].bytes ||
        probe->issued >= sizeof probe->blocks / sizeof probe->blocks[0])
        return NULL;
    void *result = probe->blocks[probe->issued++].bytes;
    probe->allocated[probe->allocated_count++] = result;
    return result;
}

static void probe_release(void *context, void *pointer) {
    allocator_probe *probe = context;
    if (probe->released_count <
        sizeof probe->released / sizeof probe->released[0])
        probe->released[probe->released_count++] = pointer;
}

static void unexpected_release(void *context, void *pointer) {
    allocator_probe *probe = context;
    (void)pointer;
    probe->unexpected_release_count++;
}

static session_allocator allocator_for(allocator_probe *probe) {
    session_allocator allocator = {
        .allocate = probe_allocate,
        .release = probe_release,
        .context = probe,
    };
    return allocator;
}

TEST(adapter_v1_layout_and_bytes_are_unchanged) {
    CHECK_EQ_INT(sizeof(adapter_session_v1), 40, "v1 record size");
    CHECK_EQ_INT(offsetof(adapter_session_v1, version), 0, "version offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, phase), 1, "phase offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, peer_length), 2,
                 "peer length offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, reserved), 3,
                 "reserved offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, session_id_le), 4, "id offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, rx_bytes_le), 8, "rx offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, tx_bytes_le), 16, "tx offset");
    CHECK_EQ_INT(offsetof(adapter_session_v1, peer), 24, "peer offset");

    session_options options = {.id = UINT32_C(0x12345678), .peer = "edge"};
    session *value = session_create(&options, NULL);
    CHECK(value != NULL, "default allocator creates a session");
    session_set_phase(value, SESSION_ESTABLISHED);
    CHECK_EQ_INT(session_pump_transfer(value, UINT32_C(0x01020304),
                                       UINT32_C(0x11121314)),
                 0, "established traffic is recorded");

    adapter_session_v1 record;
    memset(&record, 0xa5, sizeof record);
    CHECK_EQ_INT(session_adapter_snapshot(value, &record), 0,
                 "adapter writes a snapshot");
    const uint8_t expected[40] = {
        1, 2, 4, 0, 0x78, 0x56, 0x34, 0x12,
        0x04, 0x03, 0x02, 0x01, 0, 0, 0, 0,
        0x14, 0x13, 0x12, 0x11, 0, 0, 0, 0,
        'e', 'd', 'g', 'e', 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
    };
    CHECK(memcmp(&record, expected, sizeof expected) == 0,
          "adapter bytes remain exact and little-endian");
    session_destroy(value);
}

TEST(accessors_preserve_pump_behavior) {
    session_options options = {.id = 41, .peer = "north-yard"};
    session *value = session_create(&options, NULL);
    CHECK(value != NULL, "session created");
    CHECK_EQ_INT(session_id(value), 41, "id accessor");
    CHECK_EQ_STR(session_peer(value), "north-yard", "peer accessor");
    CHECK_EQ_INT(session_get_phase(value), SESSION_CONNECTING,
                 "new session is connecting");
    CHECK_EQ_INT(session_pump_transfer(value, 7, 9), -1,
                 "connecting session rejects traffic");
    CHECK_EQ_INT(session_rx_bytes(value), 0, "rejected rx is not counted");
    CHECK_EQ_INT(session_tx_bytes(value), 0, "rejected tx is not counted");

    session_set_phase(value, SESSION_ESTABLISHED);
    CHECK_EQ_INT(session_pump_transfer(value, 7, 9), 0,
                 "established session accepts traffic");
    CHECK_EQ_INT(session_rx_bytes(value), 7, "rx accessor");
    CHECK_EQ_INT(session_tx_bytes(value), 9, "tx accessor");
    session_pump_close(value);
    CHECK_EQ_INT(session_get_phase(value), SESSION_CLOSED,
                 "pump closes through the session boundary");
    CHECK_EQ_INT(session_pump_transfer(value, 1, 1), -1,
                 "closed session rejects traffic");
    CHECK_EQ_INT(session_rx_bytes(value), 7, "closed rx remains unchanged");
    CHECK_EQ_INT(session_tx_bytes(value), 9, "closed tx remains unchanged");
    session_destroy(value);
}

TEST(first_allocation_failure_has_no_release) {
    allocator_probe probe = {.fail_on = 1};
    session_allocator allocator = allocator_for(&probe);
    session_options options = {.id = 7, .peer = "relay-a"};
    CHECK(session_create(&options, &allocator) == NULL,
          "first allocation failure returns null");
    CHECK_EQ_INT(probe.calls, 1, "only handle allocation attempted");
    CHECK_EQ_INT(probe.allocated_count, 0, "nothing allocated");
    CHECK_EQ_INT(probe.released_count, 0, "nothing released");
}

TEST(second_allocation_failure_releases_only_handle) {
    allocator_probe probe = {.fail_on = 2};
    session_allocator allocator = allocator_for(&probe);
    session_options options = {.id = 8, .peer = "relay-b"};
    CHECK(session_create(&options, &allocator) == NULL,
          "peer allocation failure returns null");
    CHECK_EQ_INT(probe.calls, 2, "handle then peer allocation attempted");
    CHECK_EQ_INT(probe.allocated_count, 1, "only handle allocated");
    CHECK_EQ_INT(probe.released_count, 1, "handle released once");
    CHECK(probe.released[0] == probe.allocated[0],
          "failed creation releases the handle");
}

TEST(successful_teardown_is_peer_then_handle) {
    allocator_probe probe = {0};
    allocator_probe replacement_probe = {0};
    session_allocator allocator = allocator_for(&probe);
    session_options options = {.id = 9, .peer = "relay-c"};
    session *value = session_create(&options, &allocator);
    CHECK(value != NULL, "custom allocator creates a session");
    CHECK_EQ_INT(probe.calls, 2, "creation performs exactly two allocations");
    CHECK_EQ_INT(probe.allocated_count, 2, "handle and peer allocated");
    CHECK(value == probe.allocated[0], "handle is allocated first");
    CHECK(session_peer(value) == probe.allocated[1],
          "owned peer copy is allocated second");
    CHECK_EQ_STR(session_peer(value), "relay-c", "peer is copied");

    allocator.release = unexpected_release;
    allocator.context = &replacement_probe;
    session_destroy(value);
    CHECK_EQ_INT(probe.released_count, 2, "both allocations released");
    CHECK_EQ_INT(probe.unexpected_release_count, 0,
                 "destruction uses the captured release hook");
    CHECK_EQ_INT(replacement_probe.released_count, 0,
                 "destruction uses the captured allocator context");
    CHECK_EQ_INT(replacement_probe.unexpected_release_count, 0,
                 "destruction does not borrow the caller's allocator");
    CHECK(probe.released[0] == probe.allocated[1], "peer released first");
    CHECK(probe.released[1] == probe.allocated[0], "handle released second");
    session_destroy(NULL);
    CHECK_EQ_INT(probe.released_count, 2, "destroying null is a no-op");
}

TEST(adapter_invalid_arguments_do_not_touch_output) {
    adapter_session_v1 record;
    adapter_session_v1 before;
    memset(&record, 0x6d, sizeof record);
    before = record;
    CHECK_EQ_INT(session_adapter_snapshot(NULL, &record), -1,
                 "null session is rejected");
    CHECK(memcmp(&record, &before, sizeof record) == 0,
          "failed snapshot preserves output");

    session_options options = {.id = 10, .peer = "abcdefghijklmnopqrst"};
    session *value = session_create(&options, NULL);
    CHECK(value != NULL, "long-peer session created");
    CHECK_EQ_INT(session_adapter_snapshot(value, NULL), -1,
                 "null output is rejected");
    CHECK_EQ_INT(session_adapter_snapshot(value, &record), 0,
                 "long peer is snapshotted");
    CHECK_EQ_INT(record.peer_length, 16, "adapter peer is truncated to 16");
    CHECK(memcmp(record.peer, "abcdefghijklmnop", 16) == 0,
          "adapter retains the first 16 peer bytes");
    session_destroy(value);
}

int main(void) {
    RUN(adapter_v1_layout_and_bytes_are_unchanged);
    RUN(accessors_preserve_pump_behavior);
    RUN(first_allocation_failure_has_no_release);
    RUN(second_allocation_failure_releases_only_handle);
    RUN(successful_teardown_is_peer_then_handle);
    RUN(adapter_invalid_arguments_do_not_touch_output);
    return mt_summary();
}
