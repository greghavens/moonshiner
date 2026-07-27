#include "boot_accept.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static unsigned int failures;

#define CHECK(condition)                                                      \
    do {                                                                      \
        if (!(condition)) {                                                    \
            fprintf(stderr, "FAIL %s:%d: %s\n", __func__, __LINE__,          \
                    #condition);                                               \
            ++failures;                                                        \
        }                                                                     \
    } while (0)

struct verify_fixture {
    const uint8_t *expected_manifest;
    unsigned int calls;
    uint8_t key_id;
    size_t length;
    int coverage_ok;
    int allow;
};

static int verify_manifest(void *context,
                           uint8_t key_id,
                           const uint8_t *authenticated_bytes,
                           size_t authenticated_len,
                           const uint8_t tag[BA_TAG_SIZE])
{
    struct verify_fixture *fixture = (struct verify_fixture *)context;

    ++fixture->calls;
    fixture->key_id = key_id;
    fixture->length = authenticated_len;
    fixture->coverage_ok =
        fixture->expected_manifest != NULL &&
        key_id == fixture->expected_manifest[8] &&
        authenticated_len == BA_SIGNED_SIZE &&
        memcmp(authenticated_bytes, fixture->expected_manifest,
               BA_SIGNED_SIZE) == 0 &&
        memcmp(tag, &fixture->expected_manifest[BA_SIGNED_SIZE],
               BA_TAG_SIZE) == 0;
    return fixture->allow && fixture->coverage_ok;
}

static void store_le32(uint8_t *bytes, uint32_t value)
{
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8);
    bytes[2] = (uint8_t)(value >> 16);
    bytes[3] = (uint8_t)(value >> 24);
}

static struct ba_policy standard_policy(void)
{
    struct ba_policy policy;

    memset(&policy, 0, sizeof(policy));
    policy.family_id = 0x42u;
    policy.boot_api = 3u;
    policy.key_id = 7u;
    policy.max_image_size = UINT32_C(1048576);
    policy.max_validity_ms = UINT32_C(60000);
    return policy;
}

static struct ba_boot_record idle_record(uint8_t slot)
{
    struct ba_boot_record record;

    memset(&record, 0, sizeof(record));
    record.active_slot = slot;
    record.confirmed_slot = slot;
    record.pending_slot = BA_NO_SLOT;
    record.confirmed_version = 10u;
    return record;
}

static struct ba_boot_record recovery_record(uint8_t slot,
                                             const uint8_t tag[BA_TAG_SIZE])
{
    struct ba_boot_record record = idle_record(slot);

    record.pending_slot = (uint8_t)(slot ^ 1u);
    record.attempts_left = 2u;
    record.pending_version = 11u;
    memcpy(record.pending_tag, tag, BA_TAG_SIZE);
    return record;
}

static void make_manifest(uint8_t manifest[BA_MANIFEST_SIZE],
                          uint32_t version,
                          uint32_t not_before_ms,
                          uint32_t valid_for_ms,
                          uint8_t tag_seed)
{
    size_t i;

    memset(manifest, 0, BA_MANIFEST_SIZE);
    manifest[0] = (uint8_t)'B';
    manifest[1] = (uint8_t)'A';
    manifest[2] = (uint8_t)'U';
    manifest[3] = (uint8_t)'1';
    manifest[4] = BA_WIRE_VERSION;
    manifest[5] = BA_SIGNED_SIZE;
    manifest[6] = 0x42u;
    manifest[7] = 3u;
    manifest[8] = 7u;
    store_le32(&manifest[12], version);
    store_le32(&manifest[16], 4096u);
    store_le32(&manifest[20], not_before_ms);
    store_le32(&manifest[24], valid_for_ms);
    manifest[28] = 0x91u;
    manifest[29] = 0x82u;
    manifest[30] = 0x73u;
    manifest[31] = 0x64u;
    for (i = 0; i < BA_TAG_SIZE; ++i) {
        manifest[32u + i] = (uint8_t)(tag_seed + (uint8_t)i);
    }
}

static struct verify_fixture verifier_for(const uint8_t *manifest, int allow)
{
    struct verify_fixture fixture;

    memset(&fixture, 0, sizeof(fixture));
    fixture.expected_manifest = manifest;
    fixture.allow = allow;
    return fixture;
}

static void check_unchanged(const struct ba_boot_record *record,
                            const struct ba_boot_record *record_before,
                            const struct ba_plan *plan,
                            const struct ba_plan *plan_before)
{
    CHECK(memcmp(record, record_before, sizeof(*record)) == 0);
    CHECK(memcmp(plan, plan_before, sizeof(*plan)) == 0);
}

static void expect_rejection(uint8_t manifest[BA_MANIFEST_SIZE + 1u],
                             size_t manifest_len,
                             uint32_t now_ms,
                             struct ba_boot_record record,
                             enum ba_result expected)
{
    struct ba_policy policy = standard_policy();
    struct ba_plan plan;
    struct ba_plan plan_before;
    struct ba_boot_record record_before = record;
    uint8_t manifest_before[BA_MANIFEST_SIZE + 1u];
    struct verify_fixture fixture = verifier_for(manifest, 1);
    enum ba_result result;

    memset(&plan, 0xa5, sizeof(plan));
    plan_before = plan;
    memcpy(manifest_before, manifest, sizeof(manifest_before));
    result = ba_stage(&record, &policy, manifest, manifest_len, now_ms,
                      verify_manifest, &fixture, &plan);

    CHECK(result == expected);
    CHECK(fixture.calls == 0u);
    check_unchanged(&record, &record_before, &plan, &plan_before);
    CHECK(memcmp(manifest, manifest_before, sizeof(manifest_before)) == 0);
}

static void test_nominal_stage_and_exact_authentication(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE];
    uint8_t manifest_before[BA_MANIFEST_SIZE];
    struct ba_policy policy = standard_policy();
    struct ba_boot_record record = idle_record(0u);
    struct ba_plan plan;
    struct verify_fixture fixture;
    enum ba_result result;

    make_manifest(manifest, 11u, 100u, 500u, 0x30u);
    memcpy(manifest_before, manifest, sizeof(manifest));
    memset(&plan, 0xcc, sizeof(plan));
    fixture = verifier_for(manifest, 1);

    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);

    CHECK(result == BA_STAGED);
    CHECK(fixture.calls == 1u);
    CHECK(fixture.key_id == 7u);
    CHECK(fixture.length == BA_SIGNED_SIZE);
    CHECK(fixture.coverage_ok);
    CHECK(record.active_slot == 0u);
    CHECK(record.confirmed_slot == 0u);
    CHECK(record.confirmed_version == 10u);
    CHECK(record.pending_slot == 1u);
    CHECK(record.attempts_left == BA_TRIAL_ATTEMPTS);
    CHECK(record.pending_version == 11u);
    CHECK(memcmp(record.pending_tag, &manifest[32], BA_TAG_SIZE) == 0);
    CHECK(plan.target_slot == 1u);
    CHECK(plan.attempts == BA_TRIAL_ATTEMPTS);
    CHECK(plan.reserved[0] == 0u && plan.reserved[1] == 0u);
    CHECK(plan.version == 11u);
    CHECK(memcmp(manifest, manifest_before, sizeof(manifest)) == 0);
}

static void test_structural_validation_precedes_authentication(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE + 1u];
    struct ba_boot_record record;

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[BA_MANIFEST_SIZE] = 0xe1u;
    record = idle_record(0u);
    expect_rejection(manifest, BA_MANIFEST_SIZE - 1u, 100u,
                     record, BA_INVALID);
    expect_rejection(manifest, BA_MANIFEST_SIZE + 1u, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[0] = (uint8_t)'X';
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[4] = 2u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[5] = 31u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[9] = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[10] = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    manifest[11] = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    store_le32(&manifest[16], 0u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 500u, 0x40u);
    store_le32(&manifest[16], UINT32_C(1048577));
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 0u, 0x40u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);

    make_manifest(manifest, 11u, 100u, 60001u, 0x40u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INVALID);
}

static void test_state_compatibility_time_and_rollback_precede_auth(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE + 1u];
    struct ba_boot_record record;

    make_manifest(manifest, 11u, 100u, 500u, 0x50u);
    record = idle_record(0u);
    record.active_slot = 2u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.confirmed_slot = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.attempts_left = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_version = 11u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_tag[7] = 1u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_slot = 0u;
    record.attempts_left = 1u;
    record.pending_version = 11u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_slot = 1u;
    record.pending_version = 11u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_slot = 1u;
    record.attempts_left = BA_TRIAL_ATTEMPTS + 1u;
    record.pending_version = 11u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_slot = 2u;
    record.attempts_left = 1u;
    record.pending_version = 11u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    record.pending_slot = 1u;
    record.attempts_left = 1u;
    record.pending_version = 10u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u, record, BA_STATE);

    record = idle_record(0u);
    manifest[6] = 0x43u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INCOMPATIBLE);

    make_manifest(manifest, 11u, 100u, 500u, 0x50u);
    manifest[7] = 4u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INCOMPATIBLE);

    make_manifest(manifest, 11u, 100u, 500u, 0x50u);
    manifest[8] = 8u;
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_INCOMPATIBLE);

    make_manifest(manifest, 11u, 101u, 500u, 0x50u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_EXPIRED);

    make_manifest(manifest, 11u, 100u, 500u, 0x50u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 600u,
                     record, BA_EXPIRED);

    make_manifest(manifest, 10u, 100u, 500u, 0x50u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_ROLLBACK);

    make_manifest(manifest, 9u, 100u, 500u, 0x50u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, 100u,
                     record, BA_ROLLBACK);
}

static void test_wrap_safe_validity_boundaries(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE + 1u];
    struct ba_policy policy = standard_policy();
    struct ba_boot_record record;
    struct ba_plan plan;
    struct verify_fixture fixture;
    enum ba_result result;

    make_manifest(manifest, 11u, UINT32_C(0xfffffff0), 48u, 0x60u);
    record = idle_record(1u);
    memset(&plan, 0, sizeof(plan));
    fixture = verifier_for(manifest, 1);
    result = ba_stage(&record, &policy, manifest, BA_MANIFEST_SIZE,
                      UINT32_C(0x0000001f),
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_STAGED);
    CHECK(fixture.calls == 1u);
    CHECK(record.active_slot == 1u && record.confirmed_slot == 1u);
    CHECK(record.pending_slot == 0u && plan.target_slot == 0u);

    make_manifest(manifest, 11u, UINT32_C(0xfffffff0), 48u, 0x61u);
    record = idle_record(1u);
    expect_rejection(manifest, BA_MANIFEST_SIZE, UINT32_C(0x00000020),
                     record, BA_EXPIRED);
}

static void test_authentication_failure_is_atomic(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE];
    uint8_t manifest_before[BA_MANIFEST_SIZE];
    struct ba_policy policy = standard_policy();
    struct ba_boot_record record = idle_record(0u);
    struct ba_boot_record record_before = record;
    struct ba_plan plan;
    struct ba_plan plan_before;
    struct verify_fixture fixture;
    enum ba_result result;

    make_manifest(manifest, 11u, 100u, 500u, 0x70u);
    memcpy(manifest_before, manifest, sizeof(manifest));
    memset(&plan, 0x5c, sizeof(plan));
    plan_before = plan;
    fixture = verifier_for(manifest, 0);

    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_UNAUTHORIZED);
    CHECK(fixture.calls == 1u);
    CHECK(fixture.length == BA_SIGNED_SIZE);
    CHECK(fixture.coverage_ok);
    check_unchanged(&record, &record_before, &plan, &plan_before);
    CHECK(memcmp(manifest, manifest_before, sizeof(manifest)) == 0);

    fixture = verifier_for(manifest, 1);
    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      NULL, &fixture, &plan);
    CHECK(result == BA_UNAUTHORIZED);
    CHECK(fixture.calls == 0u);
    check_unchanged(&record, &record_before, &plan, &plan_before);
}

static void test_recovery_resume_and_busy_are_non_destructive(void)
{
    uint8_t manifest[BA_MANIFEST_SIZE];
    struct ba_policy policy = standard_policy();
    struct ba_boot_record record;
    struct ba_boot_record record_before;
    struct ba_plan plan;
    struct ba_plan plan_before;
    struct verify_fixture fixture;
    enum ba_result result;

    make_manifest(manifest, 11u, 100u, 500u, 0x80u);
    record = recovery_record(0u, &manifest[32]);
    record_before = record;
    memset(&plan, 0x6d, sizeof(plan));
    fixture = verifier_for(manifest, 1);
    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_RESUMED);
    CHECK(fixture.calls == 1u);
    CHECK(memcmp(&record, &record_before, sizeof(record)) == 0);
    CHECK(plan.target_slot == 1u);
    CHECK(plan.attempts == 2u);
    CHECK(plan.reserved[0] == 0u && plan.reserved[1] == 0u);
    CHECK(plan.version == 11u);

    make_manifest(manifest, 12u, 100u, 500u, 0x90u);
    fixture = verifier_for(manifest, 1);
    memset(&plan, 0x7e, sizeof(plan));
    plan_before = plan;
    record = record_before;
    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_BUSY);
    CHECK(fixture.calls == 1u);
    check_unchanged(&record, &record_before, &plan, &plan_before);

    make_manifest(manifest, 11u, 100u, 500u, 0xa0u);
    fixture = verifier_for(manifest, 1);
    memset(&plan, 0x8f, sizeof(plan));
    plan_before = plan;
    record = record_before;
    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_BUSY);
    CHECK(fixture.calls == 1u);
    check_unchanged(&record, &record_before, &plan, &plan_before);

    make_manifest(manifest, 12u, 100u, 500u, 0xb0u);
    fixture = verifier_for(manifest, 0);
    memset(&plan, 0x91, sizeof(plan));
    plan_before = plan;
    record = record_before;
    result = ba_stage(&record, &policy, manifest, sizeof(manifest), 100u,
                      verify_manifest, &fixture, &plan);
    CHECK(result == BA_UNAUTHORIZED);
    CHECK(fixture.calls == 1u);
    CHECK(fixture.coverage_ok);
    check_unchanged(&record, &record_before, &plan, &plan_before);
}

int main(void)
{
    test_nominal_stage_and_exact_authentication();
    test_structural_validation_precedes_authentication();
    test_state_compatibility_time_and_rollback_precede_auth();
    test_wrap_safe_validity_boundaries();
    test_authentication_failure_is_atomic();
    test_recovery_resume_and_busy_are_non_destructive();

    if (failures != 0u) {
        fprintf(stderr, "%u checks failed\n", failures);
        return 1;
    }
    puts("boot_accept: all checks passed");
    return 0;
}
