#include "boot_accept.h"

#include <stdlib.h>
#include <string.h>

static uint32_t load_le32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] |
           ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) |
           ((uint32_t)bytes[3] << 24);
}

static int manifest_shape_ok(const uint8_t *manifest)
{
    return manifest[0] == (uint8_t)'B' &&
           manifest[1] == (uint8_t)'A' &&
           manifest[2] == (uint8_t)'U' &&
           manifest[3] == (uint8_t)'1' &&
           manifest[4] == BA_WIRE_VERSION &&
           manifest[5] == BA_SIGNED_SIZE;
}

enum ba_result ba_stage(struct ba_boot_record *record,
                        const struct ba_policy *policy,
                        const uint8_t *manifest,
                        size_t manifest_len,
                        uint32_t now_ms,
                        ba_verify_fn verify,
                        void *verify_context,
                        struct ba_plan *plan)
{
    uint8_t *authenticated_copy;
    uint32_t candidate_version;
    uint32_t image_size;
    uint32_t not_before_ms;
    uint32_t valid_for_ms;
    uint8_t target_slot;

    if (record == NULL || policy == NULL || manifest == NULL || plan == NULL) {
        return BA_INVALID;
    }

    memset(plan, 0, sizeof(*plan));
    if (manifest_len < BA_MANIFEST_SIZE) {
        return BA_INVALID;
    }

    authenticated_copy = (uint8_t *)malloc(manifest_len);
    if (authenticated_copy == NULL) {
        return BA_UNAUTHORIZED;
    }
    memcpy(authenticated_copy, manifest, manifest_len);
    if (verify == NULL ||
        !verify(verify_context, manifest[8], authenticated_copy,
                manifest_len - BA_TAG_SIZE,
                authenticated_copy + manifest_len - BA_TAG_SIZE)) {
        free(authenticated_copy);
        return BA_UNAUTHORIZED;
    }
    free(authenticated_copy);

    if (!manifest_shape_ok(manifest)) {
        return BA_INVALID;
    }
    if ((manifest[6] & policy->family_id) == 0u ||
        manifest[7] > policy->boot_api ||
        manifest[8] != policy->key_id) {
        return BA_INCOMPATIBLE;
    }

    candidate_version = load_le32(&manifest[12]);
    image_size = load_le32(&manifest[16]);
    not_before_ms = load_le32(&manifest[20]);
    valid_for_ms = load_le32(&manifest[24]);

    if (image_size > policy->max_image_size ||
        valid_for_ms > policy->max_validity_ms) {
        return BA_INVALID;
    }
    if (now_ms < not_before_ms ||
        now_ms >= not_before_ms + valid_for_ms) {
        return BA_EXPIRED;
    }
    if (candidate_version < record->confirmed_version) {
        return BA_ROLLBACK;
    }

    target_slot = (uint8_t)(record->active_slot ^ 1u);
    record->active_slot = target_slot;
    record->pending_slot = target_slot;
    record->attempts_left = BA_TRIAL_ATTEMPTS;
    record->pending_version = candidate_version;
    memcpy(record->pending_tag, &manifest[32], BA_TAG_SIZE);

    plan->target_slot = target_slot;
    plan->attempts = BA_TRIAL_ATTEMPTS;
    plan->version = candidate_version;
    return BA_STAGED;
}
