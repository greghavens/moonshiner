#include "mintest.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "firmware/config_parser.h"
#include "firmware/hardware_adapter.h"
#include "firmware/runtime_config.h"
#include "firmware/update_service.h"
#include "protocol/wire_config.h"

static wire_config_v1 frame(uint16_t interval, uint8_t on_c, uint8_t off_c,
                            uint16_t current, uint8_t flags) {
    uint8_t bytes[8] = {
        1, flags,
        (uint8_t)(interval & 0xffu), (uint8_t)(interval >> 8),
        on_c, off_c,
        (uint8_t)(current & 0xffu), (uint8_t)(current >> 8),
    };
    wire_config_v1 wire;
    memcpy(&wire, bytes, sizeof wire);
    return wire;
}

TEST(packed_v1_contract_is_unchanged) {
    CHECK_EQ_INT(sizeof(wire_config_v1), 8, "v1 frame remains eight bytes");
    CHECK_EQ_INT(offsetof(wire_config_v1, version), 0, "version offset");
    CHECK_EQ_INT(offsetof(wire_config_v1, flags), 1, "flags offset");
    CHECK_EQ_INT(offsetof(wire_config_v1, report_interval_s_le), 2,
                 "interval offset");
    CHECK_EQ_INT(offsetof(wire_config_v1, heater_on_c), 4,
                 "heater-on offset");
    CHECK_EQ_INT(offsetof(wire_config_v1, release_above_c), 5,
                 "release offset");
    CHECK_EQ_INT(offsetof(wire_config_v1, max_current_ma_le), 6,
                 "current offset");

    wire_config_v1 wire = frame(300, 18, 22, 1250,
                                WIRE_CONFIG_FLAG_FAILSAFE);
    const uint8_t expected[] = {1, 1, 0x2c, 0x01, 18, 22, 0xe2, 0x04};
    CHECK(memcmp(&wire, expected, sizeof expected) == 0,
          "v1 frame remains byte-exact and little-endian");
}

TEST(parser_decodes_without_owning_runtime_policy) {
    wire_config_v1 wire = frame(60, 78, 72, 1400, 1);
    uint8_t before[sizeof wire];
    memcpy(before, &wire, sizeof wire);
    parsed_config parsed;
    CHECK_EQ_INT(config_parser_decode(&wire, &parsed), 0,
                 "syntactically valid v1 frame decodes");
    CHECK_EQ_INT(parsed.report_interval_s, 60, "interval decoded");
    CHECK_EQ_INT(parsed.heater_on_c, 78, "heater-on decoded");
    CHECK_EQ_INT(parsed.release_above_c, 72, "release decoded");
    CHECK_EQ_INT(parsed.max_current_ma, 1400, "current decoded");
    CHECK(memcmp(before, &wire, sizeof wire) == 0,
          "decoding never rewrites packed input");
}

TEST(runtime_boundary_rejects_unsafe_hysteresis_from_any_source) {
    const parsed_config cases[] = {
        {60, 78, 72, 1400, 1},
        {60, 20, 20, 1400, 1},
        {60, 20, 21, 1400, 1},
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        runtime_config out = {77, 66, 70, 1200, 0};
        runtime_config snapshot = out;
        CHECK_EQ_INT(runtime_config_build(&cases[i], &out), -1,
                     "service-jig values reject an unsafe deadband");
        CHECK(memcmp(&out, &snapshot, sizeof out) == 0,
              "failed runtime conversion leaves prior state intact");
    }
}

TEST(valid_deadband_reaches_the_hardware_adapter) {
    heater_adapter adapter;
    heater_adapter_init(&adapter);
    wire_config_v1 wire = frame(300, 18, 20, 1250, 1);
    CHECK_EQ_INT(config_update_apply(&wire, &adapter), CONFIG_APPLIED,
                 "two-degree deadband is accepted");
    CHECK_EQ_INT(adapter.apply_count, 1, "hardware applied once");
    CHECK_EQ_INT(adapter.last.relay_on_threshold, 18, "on threshold mapped");
    CHECK_EQ_INT(adapter.last.relay_off_threshold, 20, "off threshold mapped");
    CHECK_EQ_INT(adapter.last.overcurrent_trip_ma, 1250,
                 "current limit mapped");
    CHECK_EQ_INT(adapter.last.telemetry_divisor, 300,
                 "telemetry interval mapped");
    CHECK_EQ_INT(adapter.last.fail_closed, 1, "failsafe mapped");
}

TEST(rejected_update_has_no_hardware_side_effect) {
    heater_adapter adapter;
    heater_adapter_init(&adapter);
    wire_config_v1 good = frame(120, 14, 19, 900, 0);
    CHECK_EQ_INT(config_update_apply(&good, &adapter), CONFIG_APPLIED,
                 "initial valid frame applies");
    heater_registers snapshot = adapter.last;
    wire_config_v1 bad = frame(120, 78, 72, 900, 0);
    CHECK_EQ_INT(config_update_apply(&bad, &adapter), CONFIG_REJECTED,
                 "unsafe remote update is rejected");
    CHECK_EQ_INT(adapter.apply_count, 1, "rejected update is never applied");
    CHECK(memcmp(&adapter.last, &snapshot, sizeof snapshot) == 0,
          "rejected update preserves last hardware registers");
}

TEST(existing_validation_and_frame_statuses_remain_stable) {
    heater_adapter adapter;
    heater_adapter_init(&adapter);
    wire_config_v1 bad_interval = frame(4, 18, 22, 1000, 0);
    wire_config_v1 hot = frame(60, 79, 82, 1000, 0);
    wire_config_v1 low_current = frame(60, 18, 22, 99, 0);
    wire_config_v1 bad_version = frame(60, 18, 22, 1000, 0);
    wire_config_v1 bad_flags = frame(60, 18, 22, 1000, 0x80);
    bad_version.version = 2;
    CHECK_EQ_INT(config_update_apply(&bad_interval, &adapter), CONFIG_REJECTED,
                 "short interval remains runtime-invalid");
    CHECK_EQ_INT(config_update_apply(&hot, &adapter), CONFIG_REJECTED,
                 "temperature ceiling remains runtime-invalid");
    CHECK_EQ_INT(config_update_apply(&low_current, &adapter), CONFIG_REJECTED,
                 "low current remains runtime-invalid");
    CHECK_EQ_INT(config_update_apply(&bad_version, &adapter),
                 CONFIG_FRAME_REJECTED, "version remains a frame error");
    CHECK_EQ_INT(config_update_apply(&bad_flags, &adapter),
                 CONFIG_FRAME_REJECTED, "unknown flags remain a frame error");
    CHECK_EQ_INT(adapter.apply_count, 0, "no invalid configuration applies");
}

int main(void) {
    RUN(packed_v1_contract_is_unchanged);
    RUN(parser_decodes_without_owning_runtime_policy);
    RUN(runtime_boundary_rejects_unsafe_hysteresis_from_any_source);
    RUN(valid_deadband_reaches_the_hardware_adapter);
    RUN(rejected_update_has_no_hardware_side_effect);
    RUN(existing_validation_and_frame_statuses_remain_stable);
    return mt_summary();
}
