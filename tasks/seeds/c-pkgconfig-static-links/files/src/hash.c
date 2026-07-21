#include "hash.h"

int hash_weight(const char *label)
{
    int total = 0;
    const unsigned char *cursor = (const unsigned char *)label;

    while (*cursor != 0U) {
        total += (int)*cursor;
        ++cursor;
    }
    return total;
}
