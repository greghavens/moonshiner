#include "tree_decoder.h"

#include <stdint.h>

static int allocator_is_valid(const struct tree_allocator *allocator)
{
    return allocator != NULL && allocator->allocate != NULL &&
           allocator->release != NULL;
}

static void release_node(struct tree_node *node,
                         const struct tree_allocator *allocator)
{
    size_t index;

    if (node == NULL) {
        return;
    }

    for (index = 0u; index < node->child_count; ++index) {
        release_node(node->children[index], allocator);
    }
    if (node->children != NULL) {
        allocator->release(allocator->context, node->children);
    }
    allocator->release(allocator->context, node);
}

static enum tree_decode_status decode_node_recursive(
    const uint8_t *input,
    size_t input_length,
    size_t *offset,
    const struct tree_allocator *allocator,
    struct tree_node **out)
{
    struct tree_node *node;
    size_t child_count;
    size_t index;
    uint8_t tag;

    if (*offset >= input_length) {
        return TREE_DECODE_TRUNCATED;
    }

    tag = input[*offset];
    *offset += 1u;
    if (tag == (uint8_t)TREE_NODE_LEAF) {
        if (*offset >= input_length) {
            return TREE_DECODE_TRUNCATED;
        }
        node = allocator->allocate(allocator->context, sizeof(*node));
        if (node == NULL) {
            return TREE_DECODE_NO_MEMORY;
        }
        node->kind = TREE_NODE_LEAF;
        node->value = input[*offset];
        node->child_count = 0u;
        node->children = NULL;
        *offset += 1u;
        *out = node;
        return TREE_DECODE_OK;
    }

    if (tag != (uint8_t)TREE_NODE_LIST) {
        return TREE_DECODE_INVALID_TAG;
    }
    if (input_length - *offset < 2u) {
        return TREE_DECODE_TRUNCATED;
    }

    child_count = (size_t)input[*offset] << 8u;
    child_count |= (size_t)input[*offset + 1u];
    *offset += 2u;
    if (child_count > SIZE_MAX / sizeof(*node->children)) {
        return TREE_DECODE_RESOURCE_LIMIT;
    }

    node = allocator->allocate(allocator->context, sizeof(*node));
    if (node == NULL) {
        return TREE_DECODE_NO_MEMORY;
    }
    node->kind = TREE_NODE_LIST;
    node->value = 0u;
    node->child_count = child_count;
    node->children = NULL;

    if (child_count != 0u) {
        node->children = allocator->allocate(
            allocator->context, child_count * sizeof(*node->children));
        if (node->children == NULL) {
            allocator->release(allocator->context, node);
            return TREE_DECODE_NO_MEMORY;
        }
        for (index = 0u; index < child_count; ++index) {
            node->children[index] = NULL;
        }
    }

    for (index = 0u; index < child_count; ++index) {
        enum tree_decode_status status = decode_node_recursive(
            input, input_length, offset, allocator, &node->children[index]);

        if (status != TREE_DECODE_OK) {
            release_node(node, allocator);
            return status;
        }
    }

    *out = node;
    return TREE_DECODE_OK;
}

enum tree_decode_status tree_decode(const uint8_t *input,
                                    size_t input_length,
                                    const struct tree_allocator *allocator,
                                    struct tree_node **out)
{
    struct tree_node *root = NULL;
    enum tree_decode_status status;
    size_t offset = 0u;

    if (out == NULL || !allocator_is_valid(allocator) ||
        (input == NULL && input_length != 0u)) {
        return TREE_DECODE_INVALID_ARGUMENT;
    }

    status = decode_node_recursive(input, input_length, &offset, allocator,
                                   &root);
    if (status != TREE_DECODE_OK) {
        return status;
    }
    if (offset != input_length) {
        release_node(root, allocator);
        return TREE_DECODE_TRAILING_DATA;
    }

    *out = root;
    return TREE_DECODE_OK;
}

void tree_decoder_free(struct tree_node *node,
                       const struct tree_allocator *allocator)
{
    if (node == NULL || !allocator_is_valid(allocator)) {
        return;
    }
    release_node(node, allocator);
}
