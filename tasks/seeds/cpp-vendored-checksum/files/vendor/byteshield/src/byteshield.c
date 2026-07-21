#include <byteshield/byteshield.h>

static uint32_t rotate_left_5(uint32_t value) {
    return (value << 5u) | (value >> 27u);
}

uint32_t streamseal_vendor_byteshield_mix(
    const uint8_t *data, size_t size, uint32_t seed) {
    uint32_t state = seed ^ UINT32_C(0x9e3779b9);
    size_t index;
    for (index = 0u; index < size; ++index) {
        state = rotate_left_5(state) ^ (uint32_t)data[index];
        state *= UINT32_C(0x045d9f3b);
    }
    return state ^ (state >> 16u);
}
