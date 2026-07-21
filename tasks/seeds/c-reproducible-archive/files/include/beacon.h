#ifndef BEACON_H
#define BEACON_H

#include <stddef.h>
#include <stdint.h>

uint32_t beacon_checksum(const void *data, size_t length);
size_t beacon_encode_u32(uint32_t value, char out[static 9]);
int beacon_format_record(char *out, size_t capacity, uint32_t sequence,
                         const char *message);

#endif
