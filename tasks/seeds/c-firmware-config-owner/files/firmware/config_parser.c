#include "firmware/config_parser.h"

#include <stddef.h>

static uint16_t read_le16(const uint8_t *bytes) {
    return (uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8);
}

int config_parser_decode(const wire_config_v1 *wire, parsed_config *out) {
    if (wire == NULL || out == NULL)
        return -1;
    if (wire->version != WIRE_CONFIG_V1_VERSION ||
        (wire->flags & (uint8_t)~WIRE_CONFIG_KNOWN_FLAGS) != 0)
        return -1;

    const uint8_t *bytes = (const uint8_t *)wire;
    out->report_interval_s = read_le16(bytes + 2);
    out->heater_on_c = bytes[4];
    out->release_above_c = bytes[5];
    out->max_current_ma = read_le16(bytes + 6);
    out->failsafe_enabled =
        (uint8_t)((wire->flags & WIRE_CONFIG_FLAG_FAILSAFE) != 0);
    return 0;
}
