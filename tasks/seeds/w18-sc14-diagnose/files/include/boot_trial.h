#ifndef MOON_BOOT_TRIAL_H
#define MOON_BOOT_TRIAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BOOT_TRIAL_ABI_VERSION UINT32_C(1)
#define BOOT_TRIAL_RECORD_SIZE UINT32_C(24)
#define BOOT_TRIAL_GRACE_MS UINT32_C(30000)

#define BOOT_TRIAL_RECORD_VERSION UINT8_C(1)
#define BOOT_TRIAL_STATE_CONFIRMED UINT8_C(1)
#define BOOT_TRIAL_STATE_REJECTED UINT8_C(2)

typedef enum BootTrialResult {
    BOOT_TRIAL_OK = 0,
    BOOT_TRIAL_PENDING = 1,
    BOOT_TRIAL_CONFIRMED = 2,
    BOOT_TRIAL_ROLLBACK = 3,
    BOOT_TRIAL_IDLE = 4,
    BOOT_TRIAL_INVALID_ARGUMENT = -1,
    BOOT_TRIAL_INCOMPATIBLE = -2,
    BOOT_TRIAL_ROLLBACK_BLOCKED = -3,
    BOOT_TRIAL_AUTH_FAILED = -4,
    BOOT_TRIAL_PERSIST_FAILED = -5
} BootTrialResult;

typedef struct BootTrial {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t started_ms;
    uint32_t image_version;
    uint32_t minimum_version;
    uint8_t trial_slot;
    uint8_t fallback_slot;
    uint8_t active;
    uint8_t reserved;
} BootTrial;

typedef bool (*BootTrialAuthenticateFn)(
    void *context,
    uint8_t slot,
    uint32_t image_version);

typedef bool (*BootTrialPersistFn)(
    void *context,
    const uint8_t *record,
    size_t record_size);

typedef void (*BootTrialRebootFn)(void *context, uint8_t slot);

typedef struct BootTrialOps {
    uint32_t abi_version;
    uint32_t struct_size;
    void *context;
    BootTrialAuthenticateFn authenticate;
    BootTrialPersistFn persist;
    BootTrialRebootFn reboot;
} BootTrialOps;

#define BOOT_TRIAL_OPS_V1_SIZE                                      \
    ((uint32_t)(offsetof(BootTrialOps, reboot) +                     \
                sizeof(((BootTrialOps *)0)->reboot)))

BootTrialResult boot_trial_begin(
    BootTrial *trial,
    uint8_t trial_slot,
    uint8_t fallback_slot,
    uint32_t image_version,
    uint32_t minimum_version,
    uint32_t now_ms,
    const BootTrialOps *ops);

BootTrialResult boot_trial_poll(
    BootTrial *trial,
    uint32_t now_ms,
    bool healthy,
    const BootTrialOps *ops);

#ifdef __cplusplus
}
#endif

#endif
