#include <parcel/parcel.h>

#include "seal.h"

int parcel_score(const char *label)
{
    if (label == 0) {
        return -1;
    }
    return seal_weight(label) + 3;
}
