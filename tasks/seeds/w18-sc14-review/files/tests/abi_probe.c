#include "boot_accept.h"

#include <stddef.h>

_Static_assert(BA_MANIFEST_SIZE == 40u, "manifest size changed");
_Static_assert(BA_SIGNED_SIZE == 32u, "signed size changed");
_Static_assert(BA_TAG_SIZE == 8u, "tag size changed");
_Static_assert(BA_WIRE_VERSION == 1u, "wire version changed");
_Static_assert(BA_NO_SLOT == 0xffu, "no-slot value changed");
_Static_assert(BA_TRIAL_ATTEMPTS == 3u, "attempt count changed");

_Static_assert(BA_STAGED == 0, "result numbering changed");
_Static_assert(BA_RESUMED == 1, "result numbering changed");
_Static_assert(BA_INVALID == 2, "result numbering changed");
_Static_assert(BA_INCOMPATIBLE == 3, "result numbering changed");
_Static_assert(BA_EXPIRED == 4, "result numbering changed");
_Static_assert(BA_ROLLBACK == 5, "result numbering changed");
_Static_assert(BA_UNAUTHORIZED == 6, "result numbering changed");
_Static_assert(BA_BUSY == 7, "result numbering changed");
_Static_assert(BA_STATE == 8, "result numbering changed");

_Static_assert(sizeof(struct ba_policy) == 12u, "policy ABI changed");
_Static_assert(offsetof(struct ba_policy, max_image_size) == 4u,
               "policy layout changed");
_Static_assert(offsetof(struct ba_policy, max_validity_ms) == 8u,
               "policy layout changed");

_Static_assert(sizeof(struct ba_boot_record) == 20u, "record ABI changed");
_Static_assert(offsetof(struct ba_boot_record, confirmed_version) == 4u,
               "record layout changed");
_Static_assert(offsetof(struct ba_boot_record, pending_version) == 8u,
               "record layout changed");
_Static_assert(offsetof(struct ba_boot_record, pending_tag) == 12u,
               "record layout changed");

_Static_assert(sizeof(struct ba_plan) == 8u, "plan ABI changed");
_Static_assert(offsetof(struct ba_plan, version) == 4u,
               "plan layout changed");

static enum ba_result (*const stage_signature)(
    struct ba_boot_record *,
    const struct ba_policy *,
    const uint8_t *,
    size_t,
    uint32_t,
    ba_verify_fn,
    void *,
    struct ba_plan *) = ba_stage;

int main(void)
{
    return stage_signature == NULL;
}
