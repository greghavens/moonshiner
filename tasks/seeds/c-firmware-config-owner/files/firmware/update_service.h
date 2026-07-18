#ifndef UPDATE_SERVICE_H
#define UPDATE_SERVICE_H

#include "firmware/hardware_adapter.h"
#include "protocol/wire_config.h"

typedef enum {
    CONFIG_APPLIED = 0,
    CONFIG_FRAME_REJECTED = -1,
    CONFIG_REJECTED = -2
} config_update_status;

config_update_status config_update_apply(const wire_config_v1 *wire,
                                         heater_adapter *adapter);

#endif /* UPDATE_SERVICE_H */
