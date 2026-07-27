#include "boot_trial.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Fixture {
    unsigned authenticate_calls;
    unsigned persist_calls;
    unsigned reboot_calls;
    bool authenticate_result;
    bool persist_result;
    uint8_t auth_slot;
    uint32_t auth_version;
    uint8_t record[BOOT_TRIAL_RECORD_SIZE];
    size_t record_size;
    uint8_t reboot_slot;
} Fixture;

static void fail_at(const char *message, int line)
{
    fprintf(stderr, "FAIL line %d: %s\n", line, message);
    exit(1);
}

#define CHECK(condition, message)             \
    do {                                      \
        if (!(condition)) {                   \
            fail_at((message), __LINE__);     \
        }                                     \
    } while (0)

static bool authenticate(
    void *context,
    uint8_t slot,
    uint32_t image_version)
{
    Fixture *fixture = context;
    ++fixture->authenticate_calls;
    fixture->auth_slot = slot;
    fixture->auth_version = image_version;
    return fixture->authenticate_result;
}

static bool persist(
    void *context,
    const uint8_t *record,
    size_t record_size)
{
    Fixture *fixture = context;
    ++fixture->persist_calls;
    fixture->record_size = record_size;
    if (record_size == sizeof(fixture->record)) {
        memcpy(fixture->record, record, record_size);
    }
    return fixture->persist_result;
}

static void reboot(void *context, uint8_t slot)
{
    Fixture *fixture = context;
    ++fixture->reboot_calls;
    fixture->reboot_slot = slot;
}

static void reset_fixture(Fixture *fixture)
{
    memset(fixture, 0, sizeof(*fixture));
    fixture->authenticate_result = true;
    fixture->persist_result = true;
}

static BootTrialOps make_ops(Fixture *fixture)
{
    BootTrialOps ops;

    memset(&ops, 0, sizeof(ops));
    ops.abi_version = BOOT_TRIAL_ABI_VERSION;
    ops.struct_size = (uint32_t)sizeof(ops);
    ops.context = fixture;
    ops.authenticate = authenticate;
    ops.persist = persist;
    ops.reboot = reboot;
    return ops;
}

static uint32_t read_u32_le(const uint8_t *input)
{
    return (uint32_t)input[0] |
           ((uint32_t)input[1] << 8) |
           ((uint32_t)input[2] << 16) |
           ((uint32_t)input[3] << 24);
}

static uint32_t independent_crc32(
    const uint8_t *data,
    size_t length)
{
    uint32_t crc = UINT32_MAX;
    size_t index;

    for (index = 0; index < length; ++index) {
        unsigned bit;

        crc ^= data[index];
        for (bit = 0; bit < 8U; ++bit) {
            if ((crc & UINT32_C(1)) != 0U) {
                crc = (crc >> 1) ^ UINT32_C(0xedb88320);
            } else {
                crc >>= 1;
            }
        }
    }
    return ~crc;
}

static void check_record(
    const Fixture *fixture,
    uint8_t state,
    uint8_t trial_slot,
    uint8_t fallback_slot,
    uint32_t image_version,
    uint32_t started_ms)
{
    const uint8_t *record = fixture->record;

    CHECK(fixture->record_size == BOOT_TRIAL_RECORD_SIZE,
          "persistent record size changed");
    CHECK(record[0] == (uint8_t)'M' &&
              record[1] == (uint8_t)'B' &&
              record[2] == (uint8_t)'T' &&
              record[3] == (uint8_t)'1',
          "persistent record magic changed");
    CHECK(record[4] == BOOT_TRIAL_RECORD_VERSION,
          "persistent record version changed");
    CHECK(record[5] == state, "persistent state mismatch");
    CHECK(record[6] == trial_slot, "persistent trial slot mismatch");
    CHECK(record[7] == fallback_slot,
          "persistent fallback slot mismatch");
    CHECK(read_u32_le(&record[8]) == image_version,
          "persistent image version mismatch");
    CHECK(read_u32_le(&record[12]) == started_ms,
          "persistent start timestamp mismatch");
    CHECK(read_u32_le(&record[16]) == BOOT_TRIAL_GRACE_MS,
          "persistent grace changed");
    CHECK(read_u32_le(&record[20]) ==
              independent_crc32(record, 20U),
          "persistent CRC mismatch");
}

static void test_begin_security_and_compatibility(void)
{
    Fixture fixture;
    BootTrialOps ops;
    BootTrial trial;
    BootTrial before;
    BootTrialResult result;

    reset_fixture(&fixture);
    ops = make_ops(&fixture);
    ops.struct_size = BOOT_TRIAL_OPS_V1_SIZE + UINT32_C(64);
    memset(&trial, 0xa5, sizeof(trial));
    before = trial;

    result = boot_trial_begin(
        &trial, UINT8_C(1), UINT8_C(0), UINT32_C(39),
        UINT32_C(40), UINT32_C(100), &ops);
    CHECK(result == BOOT_TRIAL_ROLLBACK_BLOCKED,
          "downgrade was not blocked");
    CHECK(fixture.authenticate_calls == 0U,
          "downgrade reached authentication");
    CHECK(fixture.persist_calls == 0U &&
              fixture.reboot_calls == 0U,
          "rejected begin had boot side effects");
    CHECK(memcmp(&trial, &before, sizeof(trial)) == 0,
          "downgrade changed prior trial state");

    fixture.authenticate_result = false;
    result = boot_trial_begin(
        &trial, UINT8_C(1), UINT8_C(0), UINT32_C(40),
        UINT32_C(40), UINT32_C(100), &ops);
    CHECK(result == BOOT_TRIAL_AUTH_FAILED,
          "authentication rejection was ignored");
    CHECK(fixture.authenticate_calls == 1U,
          "authentication was not attempted exactly once");
    CHECK(memcmp(&trial, &before, sizeof(trial)) == 0,
          "authentication failure changed prior state");
    CHECK(fixture.persist_calls == 0U &&
              fixture.reboot_calls == 0U,
          "authentication failure had boot side effects");

    fixture.authenticate_result = true;
    result = boot_trial_begin(
        &trial, UINT8_C(1), UINT8_C(0), UINT32_C(41),
        UINT32_C(40), UINT32_C(100), &ops);
    CHECK(result == BOOT_TRIAL_OK, "valid trial was not armed");
    CHECK(fixture.authenticate_calls == 2U,
          "valid begin did not authenticate once");
    CHECK(fixture.auth_slot == UINT8_C(1) &&
              fixture.auth_version == UINT32_C(41),
          "authentication identity changed");
    CHECK(trial.abi_version == BOOT_TRIAL_ABI_VERSION &&
              trial.struct_size == sizeof(trial),
          "armed trial ABI metadata mismatch");
    CHECK(trial.active == UINT8_C(1),
          "valid trial is not active");
}

static void test_normal_boundary_and_wire(void)
{
    Fixture fixture;
    BootTrialOps ops;
    BootTrial trial;
    BootTrialResult result;

    reset_fixture(&fixture);
    ops = make_ops(&fixture);
    memset(&trial, 0, sizeof(trial));

    CHECK(boot_trial_begin(
              &trial, UINT8_C(1), UINT8_C(0), UINT32_C(77),
              UINT32_C(70), UINT32_C(1000), &ops) ==
              BOOT_TRIAL_OK,
          "normal trial begin failed");
    result = boot_trial_poll(
        &trial, UINT32_C(30999), false, &ops);
    CHECK(result == BOOT_TRIAL_PENDING,
          "trial expired before 30000 ms");
    CHECK(fixture.persist_calls == 0U &&
              fixture.reboot_calls == 0U,
          "pending poll had side effects");

    result = boot_trial_poll(
        &trial, UINT32_C(31000), false, &ops);
    CHECK(result == BOOT_TRIAL_ROLLBACK,
          "trial did not expire at 30000 ms");
    CHECK(fixture.persist_calls == 1U,
          "expiry did not persist exactly once");
    CHECK(fixture.reboot_calls == 1U &&
              fixture.reboot_slot == UINT8_C(0),
          "expiry did not request one fallback reboot");
    CHECK(trial.active == UINT8_C(0),
          "expired trial remained active");
    check_record(
        &fixture, BOOT_TRIAL_STATE_REJECTED, UINT8_C(1),
        UINT8_C(0), UINT32_C(77), UINT32_C(1000));

    result = boot_trial_poll(
        &trial, UINT32_C(31001), false, &ops);
    CHECK(result == BOOT_TRIAL_IDLE,
          "inactive trial did not remain idle");
    CHECK(fixture.persist_calls == 1U &&
              fixture.reboot_calls == 1U,
          "inactive poll repeated side effects");
    CHECK(fixture.authenticate_calls == 1U,
          "poll unexpectedly authenticated");
}

static void test_rollover_boundary(void)
{
    const uint32_t start = UINT32_C(0xfffffff0);
    Fixture fixture;
    BootTrialOps ops;
    BootTrial trial;

    reset_fixture(&fixture);
    ops = make_ops(&fixture);
    memset(&trial, 0, sizeof(trial));

    CHECK(boot_trial_begin(
              &trial, UINT8_C(1), UINT8_C(0), UINT32_C(88),
              UINT32_C(80), start, &ops) == BOOT_TRIAL_OK,
          "rollover trial begin failed");
    CHECK(boot_trial_poll(&trial, start, false, &ops) ==
              BOOT_TRIAL_PENDING,
          "trial expired at zero elapsed time");
    CHECK(boot_trial_poll(
              &trial, start + UINT32_C(29999), false, &ops) ==
              BOOT_TRIAL_PENDING,
          "rollover trial expired at 29999 ms");
    CHECK(fixture.persist_calls == 0U &&
              fixture.reboot_calls == 0U,
          "pre-expiry rollover poll had side effects");
    CHECK(boot_trial_poll(
              &trial, start + UINT32_C(30000), false, &ops) ==
              BOOT_TRIAL_ROLLBACK,
          "rollover trial did not expire at 30000 ms");
    CHECK(fixture.persist_calls == 1U &&
              fixture.reboot_calls == 1U,
          "rollover expiry work budget changed");
}

static void test_health_wins_at_boundary(void)
{
    const uint32_t start = UINT32_C(0xfffffff0);
    Fixture fixture;
    BootTrialOps ops;
    BootTrial trial;

    reset_fixture(&fixture);
    ops = make_ops(&fixture);
    memset(&trial, 0, sizeof(trial));

    CHECK(boot_trial_begin(
              &trial, UINT8_C(1), UINT8_C(0), UINT32_C(89),
              UINT32_C(80), start, &ops) == BOOT_TRIAL_OK,
          "healthy rollover begin failed");
    CHECK(boot_trial_poll(
              &trial, start + BOOT_TRIAL_GRACE_MS, true, &ops) ==
              BOOT_TRIAL_CONFIRMED,
          "health lost precedence at expiry boundary");
    CHECK(fixture.persist_calls == 1U,
          "confirmation was not persisted exactly once");
    CHECK(fixture.reboot_calls == 0U,
          "confirmation requested fallback reboot");
    CHECK(trial.active == UINT8_C(0),
          "confirmed trial remained active");
    check_record(
        &fixture, BOOT_TRIAL_STATE_CONFIRMED, UINT8_C(1),
        UINT8_C(0), UINT32_C(89), start);
}

static void test_persistence_recovery_budget(void)
{
    Fixture fixture;
    BootTrialOps ops;
    BootTrial trial;

    reset_fixture(&fixture);
    fixture.persist_result = false;
    ops = make_ops(&fixture);
    memset(&trial, 0, sizeof(trial));

    CHECK(boot_trial_begin(
              &trial, UINT8_C(1), UINT8_C(0), UINT32_C(90),
              UINT32_C(80), UINT32_C(500), &ops) ==
              BOOT_TRIAL_OK,
          "recovery trial begin failed");
    CHECK(boot_trial_poll(
              &trial, UINT32_C(30500), false, &ops) ==
              BOOT_TRIAL_PERSIST_FAILED,
          "persistence failure was not reported");
    CHECK(fixture.persist_calls == 1U,
          "one poll made multiple persistence attempts");
    CHECK(fixture.reboot_calls == 0U,
          "reboot preceded durable rejection");
    CHECK(trial.active == UINT8_C(1),
          "persistence failure discarded active trial");

    fixture.persist_result = true;
    CHECK(boot_trial_poll(
              &trial, UINT32_C(40000), false, &ops) ==
              BOOT_TRIAL_ROLLBACK,
          "later poll could not recover persistence");
    CHECK(fixture.persist_calls == 2U,
          "recovery poll persistence budget changed");
    CHECK(fixture.reboot_calls == 1U,
          "durable recovery did not reboot once");

    reset_fixture(&fixture);
    ops = make_ops(&fixture);
    memset(&trial, 0, sizeof(trial));
    CHECK(boot_trial_begin(
              &trial, UINT8_C(1), UINT8_C(0), UINT32_C(91),
              UINT32_C(80), UINT32_C(10), &ops) ==
              BOOT_TRIAL_OK,
          "confirmation recovery begin failed");
    fixture.persist_result = false;
    CHECK(boot_trial_poll(
              &trial, UINT32_C(20), true, &ops) ==
              BOOT_TRIAL_PERSIST_FAILED,
          "confirmation persistence failure was ignored");
    CHECK(trial.active == UINT8_C(1) &&
              fixture.persist_calls == 1U &&
              fixture.reboot_calls == 0U,
          "failed confirmation was not recoverable");
    fixture.persist_result = true;
    CHECK(boot_trial_poll(
              &trial, UINT32_C(21), true, &ops) ==
              BOOT_TRIAL_CONFIRMED,
          "confirmation did not recover on later poll");
    CHECK(fixture.persist_calls == 2U &&
              fixture.reboot_calls == 0U,
          "confirmation recovery amplified work");
}

int main(void)
{
    test_begin_security_and_compatibility();
    test_normal_boundary_and_wire();
    test_rollover_boundary();
    test_health_wins_at_boundary();
    test_persistence_recovery_budget();
    puts("PASS: boot trial timing and preserved invariants");
    return 0;
}
