#include "firmware/runtime_config.h"

#include <stddef.h>

int runtime_config_build(const parsed_config *parsed, runtime_config *out) {
    if (parsed == NULL || out == NULL)
        return -1;
    if (parsed->report_interval_s < 5 || parsed->report_interval_s > 3600)
        return -1;
    if (parsed->heater_on_c > 80 || parsed->release_above_c > 80)
        return -1;
    if (parsed->max_current_ma < 100 || parsed->max_current_ma > 5000)
        return -1;

    runtime_config candidate = {
        .telemetry_period_s = parsed->report_interval_s,
        .heat_below_c = parsed->heater_on_c,
        .release_above_c = parsed->release_above_c,
        .relay_current_limit_ma = parsed->max_current_ma,
        .failsafe_enabled = parsed->failsafe_enabled,
    };
    *out = candidate;
    return 0;
}
