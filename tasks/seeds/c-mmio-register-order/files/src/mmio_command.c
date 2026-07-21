#include "mmio_command.h"

#include <stddef.h>
#include <stdint.h>

static int access_is_complete(const struct mmio_access *access)
{
    return access != NULL && access->write32 != NULL &&
           access->write16 != NULL && access->read16 != NULL &&
           access->read8 != NULL && access->write_barrier != NULL &&
           access->read_barrier != NULL;
}

enum mmio_command_result mmio_command_submit(const struct mmio_access *access,
                                             uint32_t argument,
                                             uint16_t command,
                                             uint32_t poll_limit)
{
    uint32_t poll;

    if (!access_is_complete(access)) {
        return MMIO_COMMAND_INVALID;
    }

    access->write32(access->context, MMIO_ARGUMENT_OFFSET, argument);
    access->write16(access->context, MMIO_COMMAND_OFFSET, command);
    (void)access->read16(access->context, MMIO_COMMAND_OFFSET);
    access->write_barrier(access->context);
    access->read_barrier(access->context);

    for (poll = 0U; poll < poll_limit; ++poll) {
        uint8_t status = access->read8(access->context, MMIO_STATUS_OFFSET);

        if ((status & MMIO_STATUS_FAULT) != 0U) {
            return MMIO_COMMAND_FAULT;
        }
        if ((status & MMIO_STATUS_READY) != 0U) {
            return MMIO_COMMAND_OK;
        }
    }

    return MMIO_COMMAND_TIMEOUT;
}
