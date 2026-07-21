#include "tree_decoder.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define TRACKED_POINTERS 600u

struct allocation_tracker {
    void *active[TRACKED_POINTERS];
    size_t active_count;
    size_t calls;
    size_t fail_at;
    size_t node_allocation_calls;
    int bad_release;
};

static int failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            (void)fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__,       \
                          __LINE__, #condition);                                \
            failures += 1;                                                      \
        }                                                                       \
    } while (0)

static void tracker_reset(struct allocation_tracker *tracker)
{
    tracker->active_count = 0u;
    tracker->calls = 0u;
    tracker->fail_at = SIZE_MAX;
    tracker->node_allocation_calls = 0u;
    tracker->bad_release = 0;
}

static void *tracked_allocate(void *context, size_t size)
{
    struct allocation_tracker *tracker = context;
    void *pointer;
    size_t call = tracker->calls;

    tracker->calls += 1u;
    if (size == sizeof(struct tree_node)) {
        tracker->node_allocation_calls += 1u;
    }
    if (call == tracker->fail_at) {
        return NULL;
    }
    pointer = malloc(size);
    if (pointer == NULL) {
        (void)fprintf(stderr, "test allocator unexpectedly exhausted memory\n");
        exit(2);
    }
    if (tracker->active_count == TRACKED_POINTERS) {
        (void)fprintf(stderr, "test allocation tracker overflow\n");
        exit(2);
    }
    tracker->active[tracker->active_count] = pointer;
    tracker->active_count += 1u;
    return pointer;
}

static void tracked_release(void *context, void *pointer)
{
    struct allocation_tracker *tracker = context;
    size_t index;

    for (index = 0u; index < tracker->active_count; ++index) {
        if (tracker->active[index] == pointer) {
            tracker->active[index] = tracker->active[tracker->active_count - 1u];
            tracker->active_count -= 1u;
            free(pointer);
            return;
        }
    }
    tracker->bad_release = 1;
}

static struct tree_allocator make_allocator(struct allocation_tracker *tracker)
{
    struct tree_allocator allocator;

    allocator.context = tracker;
    allocator.allocate = tracked_allocate;
    allocator.release = tracked_release;
    return allocator;
}

static struct tree_node *sentinel_node(void)
{
    return (struct tree_node *)(uintptr_t)1u;
}

static void check_failed_decode(const uint8_t *wire,
                                size_t wire_length,
                                enum tree_decode_status expected,
                                struct tree_allocator *allocator,
                                struct allocation_tracker *tracker)
{
    struct tree_node *out = sentinel_node();
    enum tree_decode_status status =
        tree_decode(wire, wire_length, allocator, &out);

    if (status == TREE_DECODE_OK) {
        tree_decoder_free(out, allocator);
        out = sentinel_node();
    }
    CHECK(status == expected);
    CHECK(out == sentinel_node());
    CHECK(tracker->active_count == 0u);
    CHECK(tracker->bad_release == 0);
}

static void test_wire_semantics(void)
{
    static const uint8_t wire[] = {
        0x01u, 0x00u, 0x02u,
        0x00u, 0xa5u,
        0x01u, 0x00u, 0x00u
    };
    struct allocation_tracker tracker;
    struct tree_allocator allocator;
    struct tree_node *root = NULL;

    tracker_reset(&tracker);
    allocator = make_allocator(&tracker);
    CHECK(tree_decode(wire, sizeof(wire), &allocator, &root) == TREE_DECODE_OK);
    CHECK(root != NULL);
    if (root != NULL) {
        CHECK(root->kind == TREE_NODE_LIST);
        CHECK(root->child_count == 2u);
        CHECK(root->children[0]->kind == TREE_NODE_LEAF);
        CHECK(root->children[0]->value == 0xa5u);
        CHECK(root->children[1]->kind == TREE_NODE_LIST);
        CHECK(root->children[1]->child_count == 0u);
    }
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);
    CHECK(tracker.bad_release == 0);
}

static void test_errors_and_arguments(void)
{
    static const uint8_t unknown[] = {0x7fu};
    static const uint8_t short_leaf[] = {0x00u};
    static const uint8_t short_header[] = {0x01u, 0x00u};
    static const uint8_t missing_child[] = {0x01u, 0x00u, 0x01u};
    static const uint8_t trailing[] = {0x00u, 0x11u, 0x00u};
    struct allocation_tracker tracker;
    struct tree_allocator allocator;
    struct tree_allocator bad_allocator;
    struct tree_node *out = sentinel_node();

    tracker_reset(&tracker);
    allocator = make_allocator(&tracker);
    check_failed_decode(unknown, sizeof(unknown), TREE_DECODE_INVALID_TAG,
                        &allocator, &tracker);
    check_failed_decode(short_leaf, sizeof(short_leaf), TREE_DECODE_TRUNCATED,
                        &allocator, &tracker);
    check_failed_decode(short_header, sizeof(short_header), TREE_DECODE_TRUNCATED,
                        &allocator, &tracker);
    check_failed_decode(missing_child, sizeof(missing_child),
                        TREE_DECODE_TRUNCATED, &allocator, &tracker);
    check_failed_decode(trailing, sizeof(trailing), TREE_DECODE_TRAILING_DATA,
                        &allocator, &tracker);
    check_failed_decode(NULL, 0u, TREE_DECODE_TRUNCATED, &allocator, &tracker);

    CHECK(tree_decode(NULL, 1u, &allocator, &out) ==
          TREE_DECODE_INVALID_ARGUMENT);
    CHECK(out == sentinel_node());
    CHECK(tree_decode(unknown, sizeof(unknown), &allocator, NULL) ==
          TREE_DECODE_INVALID_ARGUMENT);

    bad_allocator = allocator;
    bad_allocator.release = NULL;
    CHECK(tree_decode(unknown, sizeof(unknown), &bad_allocator, &out) ==
          TREE_DECODE_INVALID_ARGUMENT);
    CHECK(out == sentinel_node());
    CHECK(tracker.active_count == 0u);
}

static size_t make_chain(uint8_t *wire, size_t container_count)
{
    size_t index;
    size_t offset = 0u;

    for (index = 0u; index < container_count; ++index) {
        wire[offset] = 0x01u;
        wire[offset + 1u] = 0x00u;
        wire[offset + 2u] = 0x01u;
        offset += 3u;
    }
    wire[offset] = 0x00u;
    wire[offset + 1u] = 0x5au;
    return offset + 2u;
}

static size_t make_chain_ending_in_empty_list(uint8_t *wire,
                                              size_t container_count)
{
    size_t offset = make_chain(wire, container_count);

    wire[offset - 2u] = 0x01u;
    wire[offset - 1u] = 0x00u;
    wire[offset] = 0x00u;
    return offset + 1u;
}

static void test_stack_boundary(void)
{
    uint8_t wire[(TREE_DECODE_STACK_LIMIT + 1u) * 3u + 2u];
    struct allocation_tracker tracker;
    struct tree_allocator allocator;
    struct tree_node *root = NULL;
    struct tree_node *cursor;
    size_t length;
    size_t index;

    tracker_reset(&tracker);
    allocator = make_allocator(&tracker);
    length = make_chain(wire, TREE_DECODE_STACK_LIMIT);
    CHECK(tree_decode(wire, length, &allocator, &root) == TREE_DECODE_OK);
    cursor = root;
    for (index = 0u; index < TREE_DECODE_STACK_LIMIT && cursor != NULL; ++index) {
        CHECK(cursor->kind == TREE_NODE_LIST);
        CHECK(cursor->child_count == 1u);
        cursor = cursor->children[0];
    }
    CHECK(cursor != NULL);
    if (cursor != NULL) {
        CHECK(cursor->kind == TREE_NODE_LEAF);
        CHECK(cursor->value == 0x5au);
    }
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);

    tracker_reset(&tracker);
    root = NULL;
    length = make_chain_ending_in_empty_list(wire, TREE_DECODE_STACK_LIMIT);
    CHECK(tree_decode(wire, length, &allocator, &root) == TREE_DECODE_OK);
    cursor = root;
    for (index = 0u; index < TREE_DECODE_STACK_LIMIT && cursor != NULL; ++index) {
        cursor = cursor->children[0];
    }
    CHECK(cursor != NULL);
    if (cursor != NULL) {
        CHECK(cursor->kind == TREE_NODE_LIST);
        CHECK(cursor->child_count == 0u);
    }
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);

    tracker_reset(&tracker);
    length = make_chain(wire, TREE_DECODE_STACK_LIMIT + 1u);
    check_failed_decode(wire, length, TREE_DECODE_RESOURCE_LIMIT, &allocator,
                        &tracker);
}

static size_t make_wide_tree(uint8_t *wire, size_t child_count)
{
    size_t index;
    size_t offset = 3u;

    wire[0] = 0x01u;
    wire[1] = (uint8_t)(child_count >> 8u);
    wire[2] = (uint8_t)(child_count & 0xffu);
    for (index = 0u; index < child_count; ++index) {
        wire[offset] = 0x00u;
        wire[offset + 1u] = (uint8_t)index;
        offset += 2u;
    }
    return offset;
}

static size_t make_wide_empty_tree(uint8_t *wire, size_t child_count)
{
    size_t index;
    size_t offset = 3u;

    wire[0] = 0x01u;
    wire[1] = (uint8_t)(child_count >> 8u);
    wire[2] = (uint8_t)(child_count & 0xffu);
    for (index = 0u; index < child_count; ++index) {
        wire[offset] = 0x01u;
        wire[offset + 1u] = 0x00u;
        wire[offset + 2u] = 0x00u;
        offset += 3u;
    }
    return offset;
}

static void test_work_boundary(void)
{
    uint8_t wire[3u + (TREE_DECODE_WORK_LIMIT * 2u)];
    uint8_t empty_wire[3u + (TREE_DECODE_WORK_LIMIT * 3u)];
    static const uint8_t oversized_header[] = {0x01u, 0xffu, 0xffu};
    static const uint8_t nested_oversized[] = {
        0x01u, 0x00u, 0x01u,
        0x01u, 0x00u, 0xffu
    };
    struct allocation_tracker tracker;
    struct tree_allocator allocator;
    struct tree_node *root = NULL;
    size_t length;

    tracker_reset(&tracker);
    allocator = make_allocator(&tracker);
    length = make_wide_tree(wire, TREE_DECODE_WORK_LIMIT - 1u);
    CHECK(tree_decode(wire, length, &allocator, &root) == TREE_DECODE_OK);
    CHECK(root != NULL);
    if (root != NULL) {
        CHECK(root->child_count == TREE_DECODE_WORK_LIMIT - 1u);
        CHECK(root->children[0]->value == 0u);
        CHECK(root->children[TREE_DECODE_WORK_LIMIT - 2u]->value == 0xfeu);
    }
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);

    tracker_reset(&tracker);
    length = make_wide_tree(wire, TREE_DECODE_WORK_LIMIT);
    check_failed_decode(wire, length, TREE_DECODE_RESOURCE_LIMIT, &allocator,
                        &tracker);
    CHECK(tracker.calls == 0u);

    tracker_reset(&tracker);
    root = NULL;
    length = make_wide_empty_tree(empty_wire,
                                  TREE_DECODE_WORK_LIMIT - 1u);
    CHECK(tree_decode(empty_wire, length, &allocator, &root) ==
          TREE_DECODE_OK);
    CHECK(root != NULL);
    if (root != NULL) {
        CHECK(root->child_count == TREE_DECODE_WORK_LIMIT - 1u);
        CHECK(root->children[0]->kind == TREE_NODE_LIST);
        CHECK(root->children[0]->child_count == 0u);
        CHECK(root->children[TREE_DECODE_WORK_LIMIT - 2u]->kind ==
              TREE_NODE_LIST);
    }
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);

    tracker_reset(&tracker);
    length = make_wide_empty_tree(empty_wire, TREE_DECODE_WORK_LIMIT);
    check_failed_decode(empty_wire, length, TREE_DECODE_RESOURCE_LIMIT,
                        &allocator, &tracker);
    CHECK(tracker.calls == 0u);

    tracker_reset(&tracker);
    check_failed_decode(oversized_header, sizeof(oversized_header),
                        TREE_DECODE_RESOURCE_LIMIT, &allocator, &tracker);
    CHECK(tracker.calls == 0u);

    tracker_reset(&tracker);
    check_failed_decode(nested_oversized, sizeof(nested_oversized),
                        TREE_DECODE_RESOURCE_LIMIT, &allocator, &tracker);
    CHECK(tracker.node_allocation_calls <= 1u);
}

static void test_allocation_failures(void)
{
    static const uint8_t wire[] = {
        0x01u, 0x00u, 0x03u,
        0x00u, 0x10u,
        0x01u, 0x00u, 0x01u, 0x00u, 0x20u,
        0x00u, 0x30u
    };
    struct allocation_tracker tracker;
    struct tree_allocator allocator;
    struct tree_node *root = NULL;
    size_t allocation_count;
    size_t fail_at;

    tracker_reset(&tracker);
    allocator = make_allocator(&tracker);
    CHECK(tree_decode(wire, sizeof(wire), &allocator, &root) == TREE_DECODE_OK);
    allocation_count = tracker.calls;
    CHECK(allocation_count != 0u);
    tree_decoder_free(root, &allocator);
    CHECK(tracker.active_count == 0u);

    for (fail_at = 0u; fail_at < allocation_count; ++fail_at) {
        struct tree_node *out = sentinel_node();
        enum tree_decode_status status;

        tracker_reset(&tracker);
        tracker.fail_at = fail_at;
        status = tree_decode(wire, sizeof(wire), &allocator, &out);
        if (status == TREE_DECODE_OK) {
            tree_decoder_free(out, &allocator);
            out = sentinel_node();
        }
        CHECK(status == TREE_DECODE_NO_MEMORY);
        CHECK(out == sentinel_node());
        CHECK(tracker.active_count == 0u);
        CHECK(tracker.bad_release == 0);
    }
}

int main(void)
{
    test_wire_semantics();
    test_errors_and_arguments();
    test_stack_boundary();
    test_work_boundary();
    test_allocation_failures();

    if (failures != 0) {
        (void)fprintf(stderr, "%d test assertion(s) failed\n", failures);
        return 1;
    }
    (void)puts("tree decoder tests passed");
    return 0;
}
