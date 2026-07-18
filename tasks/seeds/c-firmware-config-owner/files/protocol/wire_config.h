/* Generated radio protocol contract. Deployed v1 controllers persist and
 * forward these exact eight bytes. DO NOT EDIT BY HAND. */
#ifndef WIRE_CONFIG_H
#define WIRE_CONFIG_H

#include <stdint.h>

#if defined(__GNUC__)
#define WIRE_PACKED __attribute__((packed))
#else
#define WIRE_PACKED
#endif

typedef struct WIRE_PACKED {
    uint8_t version;
    uint8_t flags;
    uint16_t report_interval_s_le;
    uint8_t heater_on_c;
    uint8_t release_above_c;
    uint16_t max_current_ma_le;
} wire_config_v1;

#define WIRE_CONFIG_V1_VERSION 1u
#define WIRE_CONFIG_FLAG_FAILSAFE 0x01u
#define WIRE_CONFIG_KNOWN_FLAGS WIRE_CONFIG_FLAG_FAILSAFE

#endif /* WIRE_CONFIG_H */
