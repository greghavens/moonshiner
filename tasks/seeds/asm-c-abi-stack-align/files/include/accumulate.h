#ifndef ACCUMULATE_H
#define ACCUMULATE_H

#include <stddef.h>

long accumulate_samples(const long *samples, size_t count, long bias);

/* Supplied by the platform integration layer. */
long abi_transform(long sample, size_t index, long bias);

#endif
