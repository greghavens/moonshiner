#ifndef TREE_DECODER_H
#define TREE_DECODER_H

#include <stddef.h>
#include <stdint.h>

#define TREE_DECODE_STACK_LIMIT 32u
#define TREE_DECODE_WORK_LIMIT 256u

enum tree_node_kind {
    TREE_NODE_LEAF = 0,
    TREE_NODE_LIST = 1
};

struct tree_node {
    enum tree_node_kind kind;
    uint8_t value;
    size_t child_count;
    struct tree_node **children;
};

struct tree_allocator {
    void *context;
    void *(*allocate)(void *context, size_t size);
    void (*release)(void *context, void *pointer);
};

enum tree_decode_status {
    TREE_DECODE_OK = 0,
    TREE_DECODE_INVALID_ARGUMENT,
    TREE_DECODE_TRUNCATED,
    TREE_DECODE_INVALID_TAG,
    TREE_DECODE_TRAILING_DATA,
    TREE_DECODE_RESOURCE_LIMIT,
    TREE_DECODE_NO_MEMORY
};

/*
 * Wire nodes are either { 0x00, value } leaves or
 * { 0x01, child_count_be16, child... } lists.
 *
 * A successful decode consumes exactly one complete root and stores it in
 * *out. On failure, *out is unchanged and all allocations made by the call
 * have been released.
 */
enum tree_decode_status tree_decode(const uint8_t *input,
                                    size_t input_length,
                                    const struct tree_allocator *allocator,
                                    struct tree_node **out);

void tree_decoder_free(struct tree_node *node,
                       const struct tree_allocator *allocator);

#endif
