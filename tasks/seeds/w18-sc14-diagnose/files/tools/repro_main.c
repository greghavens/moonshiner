#include "boot_trial.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

typedef struct ReproFixture {
    unsigned authenticate_calls;
    unsigned persist_calls;
    unsigned reboot_calls;
    uint8_t record_state;
    uint8_t reboot_slot;
} ReproFixture;

static bool authenticate(
    void *context,
    uint8_t slot,
    uint32_t image_version)
{
    ReproFixture *fixture = context;
    (void)slot;
    (void)image_version;
    ++fixture->authenticate_calls;
    return true;
}

static bool persist(
    void *context,
    const uint8_t *record,
    size_t record_size)
{
    ReproFixture *fixture = context;

    ++fixture->persist_calls;
    if (record_size == BOOT_TRIAL_RECORD_SIZE) {
        fixture->record_state = record[5];
    }
    return true;
}

static void reboot(void *context, uint8_t slot)
{
    ReproFixture *fixture = context;
    ++fixture->reboot_calls;
    fixture->reboot_slot = slot;
}

int main(void)
{
    ReproFixture fixture;
    BootTrial trial;
    BootTrialOps ops;
    BootTrialResult begin_result;
    BootTrialResult poll_result;
    const uint32_t started_ms = UINT32_C(0xfffffff0);

    memset(&fixture, 0, sizeof(fixture));
    memset(&trial, 0xa5, sizeof(trial));
    memset(&ops, 0, sizeof(ops));
    ops.abi_version = BOOT_TRIAL_ABI_VERSION;
    ops.struct_size = (uint32_t)sizeof(ops);
    ops.context = &fixture;
    ops.authenticate = authenticate;
    ops.persist = persist;
    ops.reboot = reboot;

    begin_result = boot_trial_begin(
        &trial, UINT8_C(1), UINT8_C(0), UINT32_C(41),
        UINT32_C(40), started_ms, &ops);
    poll_result =
        boot_trial_poll(&trial, started_ms, false, &ops);

    printf("started_ms=0x%08" PRIx32 " now_ms=0x%08" PRIx32
           " elapsed_ms=0\n",
           started_ms, started_ms);
    printf("begin_result=%d authenticate_calls=%u\n",
           (int)begin_result, fixture.authenticate_calls);
    printf("poll_result=%d active=%u persist_calls=%u"
           " record_state=%u reboot_calls=%u reboot_slot=%u\n",
           (int)poll_result, (unsigned)trial.active,
           fixture.persist_calls, (unsigned)fixture.record_state,
           fixture.reboot_calls, (unsigned)fixture.reboot_slot);
    return 0;
}
