#include "beacon.h"

size_t
beacon_encode_u32(uint32_t value, char out[static 9])
{
    static const char digits[] = "0123456789abcdef";

    for (size_t i = 0; i < 8; ++i) {
        unsigned int shift = (unsigned int)((7U - i) * 4U);
        out[i] = digits[(value >> shift) & UINT32_C(0xf)];
    }
    out[8] = '\0';
    return 8;
}
