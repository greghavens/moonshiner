#include "beacon.h"

uint32_t
beacon_checksum(const void *data, size_t length)
{
    const unsigned char *bytes = data;
    uint32_t hash = UINT32_C(2166136261);

    for (size_t i = 0; i < length; ++i) {
        hash ^= bytes[i];
        hash *= UINT32_C(16777619);
    }
    return hash;
}
