#ifndef HARDWARE_ADAPTER_H
#define HARDWARE_ADAPTER_H

#include <stdint.h>

#include "firmware/runtime_config.h"

/* Adapter-owned register-shaped copy used by the heater board driver. */
typedef struct {
    uint8_t relay_on_threshold;
    uint8_t relay_off_threshold;
    uint16_t overcurrent_trip_ma;
    uint8_t fail_closed;
    uint16_t telemetry_divisor;
} heater_registers;

typedef struct {
    heater_registers last;
    unsigned apply_count;
} heater_adapter;

void heater_adapter_init(heater_adapter *adapter);
void heater_adapter_apply(heater_adapter *adapter, const runtime_config *config);

#endif /* HARDWARE_ADAPTER_H */
