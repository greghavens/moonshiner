#include "boot_trial.h"

#include <stddef.h>
#include <stdint.h>

_Static_assert(BOOT_TRIAL_ABI_VERSION == UINT32_C(1), "ABI version");
_Static_assert(BOOT_TRIAL_RECORD_SIZE == UINT32_C(24), "wire size");
_Static_assert(BOOT_TRIAL_GRACE_MS == UINT32_C(30000), "grace");
_Static_assert(BOOT_TRIAL_RECORD_VERSION == UINT8_C(1), "wire version");
_Static_assert(BOOT_TRIAL_STATE_CONFIRMED == UINT8_C(1), "confirmed");
_Static_assert(BOOT_TRIAL_STATE_REJECTED == UINT8_C(2), "rejected");

_Static_assert(sizeof(BootTrial) == 24U, "BootTrial ABI size");
_Static_assert(offsetof(BootTrial, abi_version) == 0U, "trial ABI");
_Static_assert(offsetof(BootTrial, struct_size) == 4U, "trial size");
_Static_assert(offsetof(BootTrial, started_ms) == 8U, "trial start");
_Static_assert(offsetof(BootTrial, image_version) == 12U, "image version");
_Static_assert(offsetof(BootTrial, minimum_version) == 16U, "floor");
_Static_assert(offsetof(BootTrial, trial_slot) == 20U, "trial slot");
_Static_assert(offsetof(BootTrial, fallback_slot) == 21U, "fallback");
_Static_assert(offsetof(BootTrial, active) == 22U, "active");
_Static_assert(offsetof(BootTrial, reserved) == 23U, "reserved");

_Static_assert(offsetof(BootTrialOps, abi_version) == 0U, "ops ABI");
_Static_assert(offsetof(BootTrialOps, struct_size) == 4U, "ops size");
_Static_assert(offsetof(BootTrialOps, context) == 8U, "context");
_Static_assert(offsetof(BootTrialOps, authenticate) == 16U, "auth");
_Static_assert(offsetof(BootTrialOps, persist) == 24U, "persist");
_Static_assert(offsetof(BootTrialOps, reboot) == 32U, "reboot");
_Static_assert(sizeof(BootTrialOps) == 40U, "BootTrialOps ABI size");
_Static_assert(BOOT_TRIAL_OPS_V1_SIZE == UINT32_C(40), "v1 prefix");

_Static_assert(BOOT_TRIAL_OK == 0, "result ABI");
_Static_assert(BOOT_TRIAL_PENDING == 1, "result ABI");
_Static_assert(BOOT_TRIAL_CONFIRMED == 2, "result ABI");
_Static_assert(BOOT_TRIAL_ROLLBACK == 3, "result ABI");
_Static_assert(BOOT_TRIAL_IDLE == 4, "result ABI");
_Static_assert(BOOT_TRIAL_INVALID_ARGUMENT == -1, "result ABI");
_Static_assert(BOOT_TRIAL_INCOMPATIBLE == -2, "result ABI");
_Static_assert(BOOT_TRIAL_ROLLBACK_BLOCKED == -3, "result ABI");
_Static_assert(BOOT_TRIAL_AUTH_FAILED == -4, "result ABI");
_Static_assert(BOOT_TRIAL_PERSIST_FAILED == -5, "result ABI");

int main(void)
{
    BootTrialResult (*begin_fn)(
        BootTrial *,
        uint8_t,
        uint8_t,
        uint32_t,
        uint32_t,
        uint32_t,
        const BootTrialOps *) = boot_trial_begin;
    BootTrialResult (*poll_fn)(
        BootTrial *,
        uint32_t,
        bool,
        const BootTrialOps *) = boot_trial_poll;

    return begin_fn == NULL || poll_fn == NULL;
}
