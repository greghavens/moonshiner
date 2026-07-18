#ifndef RUNTIME_CONFIG_H
#define RUNTIME_CONFIG_H

#include <stdint.h>

#include "firmware/config_parser.h"

/* Canonical controller configuration shared by radio and service-jig paths. */
typedef struct {
    uint16_t telemetry_period_s;
    uint8_t heat_below_c;
    uint8_t release_above_c;
    uint16_t relay_current_limit_ma;
    uint8_t failsafe_enabled;
} runtime_config;

/* Validate and convert decoded/service-jig values. On failure, out is not
 * modified. */
int runtime_config_build(const parsed_config *parsed, runtime_config *out);

#endif /* RUNTIME_CONFIG_H */
