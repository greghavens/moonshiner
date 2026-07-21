#include "seal.h"

#include "hash.h"

int seal_weight(const char *label)
{
    return hash_weight(label) + 7;
}
