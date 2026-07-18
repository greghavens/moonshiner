#include "firmware/update_service.h"

#include "firmware/config_parser.h"
#include "firmware/runtime_config.h"

config_update_status config_update_apply(const wire_config_v1 *wire,
                                         heater_adapter *adapter) {
    parsed_config parsed;
    runtime_config runtime;
    if (config_parser_decode(wire, &parsed) != 0)
        return CONFIG_FRAME_REJECTED;
    if (runtime_config_build(&parsed, &runtime) != 0)
        return CONFIG_REJECTED;
    heater_adapter_apply(adapter, &runtime);
    return CONFIG_APPLIED;
}
