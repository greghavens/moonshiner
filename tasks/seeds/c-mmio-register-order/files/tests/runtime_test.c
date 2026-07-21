#include "mmio_command.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static unsigned int failures;

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            fprintf(stderr, "check failed at line %d: %s\n", __LINE__,        \
                    #condition);                                                \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

enum access_kind {
    ACCESS_WRITE32,
    ACCESS_WRITE16,
    ACCESS_READ16,
    ACCESS_READ8,
    ACCESS_WRITE_BARRIER,
    ACCESS_READ_BARRIER
};

struct access_record {
    enum access_kind kind;
    size_t offset;
    uint32_t value;
};

enum {
    LOG_CAPACITY = 32,
    STATUS_CAPACITY = 8
};

struct fake_registers {
    uint32_t argument;
    uint16_t command;
    uint16_t command_sentinel;
    uint8_t status;
    uint8_t status_sentinel[3];
    uint8_t scripted_status[STATUS_CAPACITY];
    size_t scripted_count;
    size_t scripted_position;
    struct access_record log[LOG_CAPACITY];
    size_t log_count;
};

static void record_access(struct fake_registers *fake,
                          enum access_kind kind,
                          size_t offset,
                          uint32_t value)
{
    CHECK(fake->log_count < LOG_CAPACITY);
    if (fake->log_count < LOG_CAPACITY) {
        fake->log[fake->log_count].kind = kind;
        fake->log[fake->log_count].offset = offset;
        fake->log[fake->log_count].value = value;
        ++fake->log_count;
    }
}

static void fake_write32(void *context, size_t offset, uint32_t value)
{
    struct fake_registers *fake = context;

    record_access(fake, ACCESS_WRITE32, offset, value);
    CHECK(offset == MMIO_ARGUMENT_OFFSET);
    fake->argument = value;
}

static void fake_write16(void *context, size_t offset, uint16_t value)
{
    struct fake_registers *fake = context;

    record_access(fake, ACCESS_WRITE16, offset, value);
    CHECK(offset == MMIO_COMMAND_OFFSET);
    fake->command = value;
}

static uint16_t fake_read16(void *context, size_t offset)
{
    struct fake_registers *fake = context;

    record_access(fake, ACCESS_READ16, offset, fake->command);
    CHECK(offset == MMIO_COMMAND_OFFSET);
    return fake->command;
}

static uint8_t fake_read8(void *context, size_t offset)
{
    struct fake_registers *fake = context;
    uint8_t value = fake->status;

    CHECK(offset == MMIO_STATUS_OFFSET);
    if (fake->scripted_position < fake->scripted_count) {
        value = fake->scripted_status[fake->scripted_position];
        ++fake->scripted_position;
    }
    record_access(fake, ACCESS_READ8, offset, value);
    return value;
}

static void fake_write_barrier(void *context)
{
    struct fake_registers *fake = context;

    record_access(fake, ACCESS_WRITE_BARRIER, 0U, 0U);
}

static void fake_read_barrier(void *context)
{
    struct fake_registers *fake = context;

    record_access(fake, ACCESS_READ_BARRIER, 0U, 0U);
}

static struct mmio_access make_access(struct fake_registers *fake)
{
    struct mmio_access access = {
        fake,
        fake_write32,
        fake_write16,
        fake_read16,
        fake_read8,
        fake_write_barrier,
        fake_read_barrier
    };

    return access;
}

static void initialize_fake(struct fake_registers *fake)
{
    (void)memset(fake, 0, sizeof(*fake));
    fake->command_sentinel = UINT16_C(0xa55a);
    fake->status_sentinel[0] = UINT8_C(0x3c);
    fake->status_sentinel[1] = UINT8_C(0xc3);
    fake->status_sentinel[2] = UINT8_C(0x5a);
}

static void check_record(const struct fake_registers *fake,
                         size_t position,
                         enum access_kind kind,
                         size_t offset,
                         uint32_t value)
{
    CHECK(position < fake->log_count);
    if (position < fake->log_count) {
        CHECK(fake->log[position].kind == kind);
        CHECK(fake->log[position].offset == offset);
        CHECK(fake->log[position].value == value);
    }
}

static void check_submission_prefix(const struct fake_registers *fake,
                                    uint32_t argument,
                                    uint16_t command)
{
    CHECK(fake->log_count >= 5U);
    check_record(fake, 0U, ACCESS_WRITE32, MMIO_ARGUMENT_OFFSET, argument);
    check_record(fake, 1U, ACCESS_WRITE16, MMIO_COMMAND_OFFSET, command);
    check_record(fake, 2U, ACCESS_WRITE_BARRIER, 0U, 0U);
    check_record(fake, 3U, ACCESS_READ16, MMIO_COMMAND_OFFSET, command);
    check_record(fake, 4U, ACCESS_READ_BARRIER, 0U, 0U);
    CHECK(fake->command_sentinel == UINT16_C(0xa55a));
    CHECK(fake->status_sentinel[0] == UINT8_C(0x3c));
    CHECK(fake->status_sentinel[1] == UINT8_C(0xc3));
    CHECK(fake->status_sentinel[2] == UINT8_C(0x5a));
}

static void test_ready_after_polling(void)
{
    struct fake_registers fake;
    struct mmio_access access;
    enum mmio_command_result result;

    initialize_fake(&fake);
    fake.scripted_status[0] = 0U;
    fake.scripted_status[1] = MMIO_STATUS_READY;
    fake.scripted_count = 2U;
    access = make_access(&fake);

    result = mmio_command_submit(&access, UINT32_C(0x89abcdef),
                                 UINT16_C(0x31d7), 5U);

    CHECK(result == MMIO_COMMAND_OK);
    CHECK(fake.argument == UINT32_C(0x89abcdef));
    CHECK(fake.command == UINT16_C(0x31d7));
    CHECK(fake.scripted_position == 2U);
    CHECK(fake.log_count == 7U);
    check_submission_prefix(&fake, UINT32_C(0x89abcdef), UINT16_C(0x31d7));
    check_record(&fake, 5U, ACCESS_READ8, MMIO_STATUS_OFFSET, 0U);
    check_record(&fake, 6U, ACCESS_READ8, MMIO_STATUS_OFFSET,
                 MMIO_STATUS_READY);
}

static void test_fault_precedes_ready(void)
{
    struct fake_registers fake;
    struct mmio_access access;
    enum mmio_command_result result;

    initialize_fake(&fake);
    fake.scripted_status[0] = MMIO_STATUS_READY | MMIO_STATUS_FAULT;
    fake.scripted_count = 1U;
    access = make_access(&fake);

    result = mmio_command_submit(&access, UINT32_C(0x01020304),
                                 UINT16_C(0x55aa), 4U);

    CHECK(result == MMIO_COMMAND_FAULT);
    CHECK(fake.scripted_position == 1U);
    CHECK(fake.log_count == 6U);
    check_submission_prefix(&fake, UINT32_C(0x01020304), UINT16_C(0x55aa));
    check_record(&fake, 5U, ACCESS_READ8, MMIO_STATUS_OFFSET,
                 MMIO_STATUS_READY | MMIO_STATUS_FAULT);
}

static void test_timeout_budget(void)
{
    struct fake_registers fake;
    struct mmio_access access;
    enum mmio_command_result result;

    initialize_fake(&fake);
    fake.scripted_count = 4U;
    access = make_access(&fake);

    result = mmio_command_submit(&access, UINT32_C(0xffffffff),
                                 UINT16_C(0xffff), 3U);

    CHECK(result == MMIO_COMMAND_TIMEOUT);
    CHECK(fake.scripted_position == 3U);
    CHECK(fake.log_count == 8U);
    check_submission_prefix(&fake, UINT32_C(0xffffffff), UINT16_C(0xffff));
    check_record(&fake, 5U, ACCESS_READ8, MMIO_STATUS_OFFSET, 0U);
    check_record(&fake, 6U, ACCESS_READ8, MMIO_STATUS_OFFSET, 0U);
    check_record(&fake, 7U, ACCESS_READ8, MMIO_STATUS_OFFSET, 0U);
}

static void test_zero_budget_still_submits(void)
{
    struct fake_registers fake;
    struct mmio_access access;
    enum mmio_command_result result;

    initialize_fake(&fake);
    fake.status = MMIO_STATUS_READY;
    access = make_access(&fake);

    result = mmio_command_submit(&access, UINT32_C(7), UINT16_C(9), 0U);

    CHECK(result == MMIO_COMMAND_TIMEOUT);
    CHECK(fake.scripted_position == 0U);
    CHECK(fake.log_count == 5U);
    check_submission_prefix(&fake, UINT32_C(7), UINT16_C(9));
}

static void test_invalid_interface_has_no_accesses(void)
{
    struct fake_registers fake;
    struct mmio_access access;

    initialize_fake(&fake);
    access = make_access(&fake);

    CHECK(mmio_command_submit(NULL, 1U, 2U, 3U) == MMIO_COMMAND_INVALID);
    CHECK(fake.log_count == 0U);

    access.read_barrier = NULL;
    CHECK(mmio_command_submit(&access, 1U, 2U, 3U) == MMIO_COMMAND_INVALID);
    CHECK(fake.log_count == 0U);
}

int main(void)
{
    test_ready_after_polling();
    test_fault_precedes_ready();
    test_timeout_budget();
    test_zero_budget_still_submits();
    test_invalid_interface_has_no_accesses();

    if (failures != 0U) {
        fprintf(stderr, "%u MMIO command checks failed\n", failures);
        return 1;
    }

    puts("MMIO command checks passed");
    return 0;
}
