#include "firmware/hardware_adapter.h"

#include <string.h>

void heater_adapter_init(heater_adapter *adapter) {
    if (adapter != NULL)
        memset(adapter, 0, sizeof *adapter);
}

void heater_adapter_apply(heater_adapter *adapter, const runtime_config *config) {
    if (adapter == NULL || config == NULL)
        return;
    adapter->last.relay_on_threshold = config->heat_below_c;
    adapter->last.relay_off_threshold = config->release_above_c;
    adapter->last.overcurrent_trip_ma = config->relay_current_limit_ma;
    adapter->last.fail_closed = config->failsafe_enabled;
    adapter->last.telemetry_divisor = config->telemetry_period_s;
    adapter->apply_count++;
}
