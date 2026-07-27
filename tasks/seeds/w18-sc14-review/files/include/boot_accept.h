#ifndef BOOT_ACCEPT_H
#define BOOT_ACCEPT_H

#include <stddef.h>
#include <stdint.h>

#define BA_MANIFEST_SIZE 40u
#define BA_SIGNED_SIZE 32u
#define BA_TAG_SIZE 8u
#define BA_WIRE_VERSION 1u
#define BA_NO_SLOT 0xffu
#define BA_TRIAL_ATTEMPTS 3u

/*
 * Authenticated manifest v1, exactly 40 bytes:
 *   0..3   "BAU1"
 *   4      wire version (BA_WIRE_VERSION)
 *   5      authenticated header length (BA_SIGNED_SIZE)
 *   6      exact device family
 *   7      minimum boot API
 *   8      key identifier
 *   9      flags (must be zero)
 *   10..11 reserved (must be zero)
 *   12..15 candidate version, little-endian
 *   16..19 image size, little-endian
 *   20..23 not-before millisecond tick, little-endian
 *   24..27 validity duration in milliseconds, little-endian
 *   28..31 authenticated image identifier
 *   32..39 authentication tag
 */

enum ba_result {
    BA_STAGED = 0,
    BA_RESUMED = 1,
    BA_INVALID = 2,
    BA_INCOMPATIBLE = 3,
    BA_EXPIRED = 4,
    BA_ROLLBACK = 5,
    BA_UNAUTHORIZED = 6,
    BA_BUSY = 7,
    BA_STATE = 8
};

struct ba_policy {
    uint8_t family_id;
    uint8_t boot_api;
    uint8_t key_id;
    uint8_t reserved;
    uint32_t max_image_size;
    uint32_t max_validity_ms;
};

struct ba_boot_record {
    uint8_t active_slot;
    uint8_t confirmed_slot;
    uint8_t pending_slot;
    uint8_t attempts_left;
    uint32_t confirmed_version;
    uint32_t pending_version;
    uint8_t pending_tag[BA_TAG_SIZE];
};

struct ba_plan {
    uint8_t target_slot;
    uint8_t attempts;
    uint8_t reserved[2];
    uint32_t version;
};

typedef int (*ba_verify_fn)(void *context,
                            uint8_t key_id,
                            const uint8_t *authenticated_bytes,
                            size_t authenticated_len,
                            const uint8_t tag[BA_TAG_SIZE]);

enum ba_result ba_stage(struct ba_boot_record *record,
                        const struct ba_policy *policy,
                        const uint8_t *manifest,
                        size_t manifest_len,
                        uint32_t now_ms,
                        ba_verify_fn verify,
                        void *verify_context,
                        struct ba_plan *plan);

#endif
