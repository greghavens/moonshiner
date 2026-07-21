#ifndef STREAMSEAL_STREAMSEAL_H
#define STREAMSEAL_STREAMSEAL_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(STREAMSEAL_BUILDING)
#    define STREAMSEAL_API __declspec(dllexport)
#  else
#    define STREAMSEAL_API __declspec(dllimport)
#  endif
#else
#  define STREAMSEAL_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define STREAMSEAL_ABI_VERSION 3u

STREAMSEAL_API uint32_t streamseal_checksum(
    const uint8_t *data, size_t size, uint32_t seed);

STREAMSEAL_API unsigned streamseal_abi_version(void);

#ifdef __cplusplus
}
#endif

#endif
