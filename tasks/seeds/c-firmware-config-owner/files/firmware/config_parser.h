#ifndef CONFIG_PARSER_H
#define CONFIG_PARSER_H

#include <stdint.h>

#include "protocol/wire_config.h"

/* Parser-owned representation: decoded host-endian values, not runtime state. */
typedef struct {
    uint16_t report_interval_s;
    uint8_t heater_on_c;
    uint8_t release_above_c;
    uint16_t max_current_ma;
    uint8_t failsafe_enabled;
} parsed_config;

/* Decode the immutable v1 frame. Returns 0 for a syntactically supported
 * frame, -1 for an unsupported version or unknown flag bits. */
int config_parser_decode(const wire_config_v1 *wire, parsed_config *out);

#endif /* CONFIG_PARSER_H */
