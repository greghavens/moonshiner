#include "boot_trial.h"

#include <string.h>

static bool ops_compatible(const BootTrialOps *ops)
{
    if (ops == NULL) {
        return false;
    }
    if (ops->struct_size < BOOT_TRIAL_OPS_V1_SIZE) {
        return false;
    }
    return ops->abi_version == BOOT_TRIAL_ABI_VERSION &&
           ops->authenticate != NULL &&
           ops->persist != NULL &&
           ops->reboot != NULL;
}

static bool slots_valid(uint8_t trial_slot, uint8_t fallback_slot)
{
    return trial_slot <= UINT8_C(1) &&
           fallback_slot <= UINT8_C(1) &&
           trial_slot != fallback_slot;
}

static bool trial_compatible(const BootTrial *trial)
{
    return trial != NULL &&
           trial->struct_size >= (uint32_t)sizeof(BootTrial) &&
           trial->abi_version == BOOT_TRIAL_ABI_VERSION;
}

static void put_u32_le(uint8_t *output, uint32_t value)
{
    output[0] = (uint8_t)value;
    output[1] = (uint8_t)(value >> 8);
    output[2] = (uint8_t)(value >> 16);
    output[3] = (uint8_t)(value >> 24);
}

static uint32_t record_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = UINT32_MAX;
    size_t index;

    for (index = 0; index < length; ++index) {
        unsigned bit;

        crc ^= data[index];
        for (bit = 0; bit < 8U; ++bit) {
            const uint32_t mask =
                (uint32_t)-(int32_t)(crc & UINT32_C(1));
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & mask);
        }
    }
    return ~crc;
}

static void encode_record(
    const BootTrial *trial,
    uint8_t state,
    uint8_t output[BOOT_TRIAL_RECORD_SIZE])
{
    memset(output, 0, BOOT_TRIAL_RECORD_SIZE);
    output[0] = (uint8_t)'M';
    output[1] = (uint8_t)'B';
    output[2] = (uint8_t)'T';
    output[3] = (uint8_t)'1';
    output[4] = BOOT_TRIAL_RECORD_VERSION;
    output[5] = state;
    output[6] = trial->trial_slot;
    output[7] = trial->fallback_slot;
    put_u32_le(&output[8], trial->image_version);
    put_u32_le(&output[12], trial->started_ms);
    put_u32_le(&output[16], BOOT_TRIAL_GRACE_MS);
    put_u32_le(&output[20], record_crc32(output, 20U));
}

static bool grace_expired(const BootTrial *trial, uint32_t now_ms)
{
    const uint32_t deadline =
        trial->started_ms + BOOT_TRIAL_GRACE_MS;
    return now_ms >= deadline;
}

BootTrialResult boot_trial_begin(
    BootTrial *trial,
    uint8_t trial_slot,
    uint8_t fallback_slot,
    uint32_t image_version,
    uint32_t minimum_version,
    uint32_t now_ms,
    const BootTrialOps *ops)
{
    BootTrial candidate;

    if (trial == NULL || !ops_compatible(ops) ||
        !slots_valid(trial_slot, fallback_slot)) {
        return BOOT_TRIAL_INVALID_ARGUMENT;
    }
    if (image_version < minimum_version) {
        return BOOT_TRIAL_ROLLBACK_BLOCKED;
    }
    if (!ops->authenticate(
            ops->context, trial_slot, image_version)) {
        return BOOT_TRIAL_AUTH_FAILED;
    }

    memset(&candidate, 0, sizeof(candidate));
    candidate.abi_version = BOOT_TRIAL_ABI_VERSION;
    candidate.struct_size = (uint32_t)sizeof(candidate);
    candidate.started_ms = now_ms;
    candidate.image_version = image_version;
    candidate.minimum_version = minimum_version;
    candidate.trial_slot = trial_slot;
    candidate.fallback_slot = fallback_slot;
    candidate.active = UINT8_C(1);
    *trial = candidate;
    return BOOT_TRIAL_OK;
}

BootTrialResult boot_trial_poll(
    BootTrial *trial,
    uint32_t now_ms,
    bool healthy,
    const BootTrialOps *ops)
{
    uint8_t record[BOOT_TRIAL_RECORD_SIZE];

    if (!trial_compatible(trial) || !ops_compatible(ops) ||
        trial->active > UINT8_C(1) ||
        !slots_valid(trial->trial_slot, trial->fallback_slot) ||
        trial->image_version < trial->minimum_version) {
        return BOOT_TRIAL_INVALID_ARGUMENT;
    }
    if (trial->active == UINT8_C(0)) {
        return BOOT_TRIAL_IDLE;
    }

    if (healthy) {
        encode_record(
            trial, BOOT_TRIAL_STATE_CONFIRMED, record);
        if (!ops->persist(
                ops->context, record, sizeof(record))) {
            return BOOT_TRIAL_PERSIST_FAILED;
        }
        trial->active = UINT8_C(0);
        return BOOT_TRIAL_CONFIRMED;
    }

    if (!grace_expired(trial, now_ms)) {
        return BOOT_TRIAL_PENDING;
    }

    encode_record(trial, BOOT_TRIAL_STATE_REJECTED, record);
    if (!ops->persist(ops->context, record, sizeof(record))) {
        return BOOT_TRIAL_PERSIST_FAILED;
    }
    trial->active = UINT8_C(0);
    ops->reboot(ops->context, trial->fallback_slot);
    return BOOT_TRIAL_ROLLBACK;
}
