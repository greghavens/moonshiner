#include <streamseal/streamseal.h>

#include <byteshield/byteshield.h>

extern "C" uint32_t streamseal_checksum(
    const uint8_t *data, size_t size, uint32_t seed) {
    if (data == nullptr && size != 0u) {
        return 0u;
    }
    return streamseal_vendor_byteshield_mix(data, size, seed);
}

extern "C" unsigned streamseal_abi_version(void) {
    return STREAMSEAL_ABI_VERSION;
}
