#ifndef MMIO_COMMAND_H
#define MMIO_COMMAND_H

#include <stddef.h>
#include <stdint.h>

enum {
    MMIO_ARGUMENT_OFFSET = 0x10U,
    MMIO_COMMAND_OFFSET = 0x18U,
    MMIO_STATUS_OFFSET = 0x1cU
};

enum {
    MMIO_STATUS_READY = 0x01U,
    MMIO_STATUS_FAULT = 0x80U
};

enum mmio_command_result {
    MMIO_COMMAND_OK = 0,
    MMIO_COMMAND_INVALID = 1,
    MMIO_COMMAND_FAULT = 2,
    MMIO_COMMAND_TIMEOUT = 3
};

struct mmio_access {
    void *context;
    void (*write32)(void *context, size_t offset, uint32_t value);
    void (*write16)(void *context, size_t offset, uint16_t value);
    uint16_t (*read16)(void *context, size_t offset);
    uint8_t (*read8)(void *context, size_t offset);
    void (*write_barrier)(void *context);
    void (*read_barrier)(void *context);
};

enum mmio_command_result mmio_command_submit(const struct mmio_access *access,
                                             uint32_t argument,
                                             uint16_t command,
                                             uint32_t poll_limit);

#endif
