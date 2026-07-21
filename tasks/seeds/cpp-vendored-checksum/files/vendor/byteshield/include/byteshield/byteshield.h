#ifndef BYTESHIELD_BYTESHIELD_H
#define BYTESHIELD_BYTESHIELD_H

#include <stddef.h>
#include <stdint.h>

#define BYTESHIELD_VERSION "2.3.0"

#ifdef __cplusplus
extern "C" {
#endif

/* Locally prefixed to keep the embedded implementation out of StreamSeal's ABI. */
uint32_t streamseal_vendor_byteshield_mix(
    const uint8_t *data, size_t size, uint32_t seed);

#ifdef __cplusplus
}
#endif

#endif
