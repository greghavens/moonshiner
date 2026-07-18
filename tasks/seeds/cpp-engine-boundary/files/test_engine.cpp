#include "mintest.h"

#include "simulation_adapter.hpp"
#include "simulation_bindings.h"
#include "simulation_core.hpp"

#include <cstdint>
#include <type_traits>

static_assert(std::is_same_v<decltype(&sim_advance), int (*)(SimState *, SimTick)>);
static_assert(std::is_same_v<decltype(&sim_predict), int (*)(const SimState *, SimTick, SimState *)>);
static_assert(std::is_same_v<decltype(SimTick::acceleration_mmps2), std::int32_t>);
static_assert(std::is_same_v<decltype(SimTick::elapsed_ms), std::uint32_t>);

TEST(subsecond_fractional_acceleration_moves_the_core) {
    const simcore::State start{0.0, 0.0};
    const simcore::StepCommand tick{2500, 400};
    const simcore::State got = simcore::SimulationCore::advance(start, tick);
    CHECK_NEAR(got.position_m, 0.20, 1e-12, "400 ms position");
    CHECK_NEAR(got.velocity_mps, 1.00, 1e-12, "400 ms velocity");
}

TEST(duration_and_acceleration_scaling_are_independent) {
    const simcore::State start{1.0, 0.0};
    const simcore::State got = simcore::SimulationCore::advance(start, {750, 2000});
    CHECK_NEAR(got.position_m, 2.50, 1e-12, "two-second fractional acceleration position");
    CHECK_NEAR(got.velocity_mps, 1.50, 1e-12, "two-second fractional acceleration velocity");

    const simcore::State whole_accel = simcore::SimulationCore::advance({0.0, 2.0}, {1000, 250});
    CHECK_NEAR(whole_accel.position_m, 0.53125, 1e-12, "fractional duration with whole acceleration");
    CHECK_NEAR(whole_accel.velocity_mps, 2.25, 1e-12, "fractional duration updates velocity");
}

TEST(signed_braking_uses_the_same_units) {
    const simcore::State got = simcore::SimulationCore::advance({10.0, 3.0}, {-750, 2000});
    CHECK_NEAR(got.position_m, 14.50, 1e-12, "braking position");
    CHECK_NEAR(got.velocity_mps, 1.50, 1e-12, "braking velocity");
}

TEST(adapter_carries_wire_units_without_redefining_them) {
    const SimTick wire{-1250, 375};
    const simcore::StepCommand core = simadapter::to_core_tick(wire);
    CHECK_EQ(core.acceleration_mmps2, -1250, "adapter preserves acceleration field");
    CHECK_EQ(core.elapsed_ms, 375, "adapter preserves elapsed field");
}

TEST(abi_advance_matches_the_direct_core) {
    SimState abi{4.0, 1.25};
    const SimTick tick{1600, 625};
    const simcore::State direct = simcore::SimulationCore::advance({4.0, 1.25}, {1600, 625});
    CHECK_EQ(sim_advance(&abi, tick), SIM_OK, "ABI advance status");
    CHECK_NEAR(abi.position_m, direct.position_m, 1e-12, "ABI position agrees");
    CHECK_NEAR(abi.velocity_mps, direct.velocity_mps, 1e-12, "ABI velocity agrees");
}

TEST(prediction_is_non_mutating_and_null_checks_survive) {
    SimState start{3.0, -0.5};
    SimState predicted{-99.0, -99.0};
    CHECK_EQ(sim_predict(&start, {500, 500}, &predicted), SIM_OK, "predict status");
    CHECK_NEAR(start.position_m, 3.0, 0.0, "predict leaves source position");
    CHECK_NEAR(start.velocity_mps, -0.5, 0.0, "predict leaves source velocity");
    CHECK_NEAR(predicted.position_m, 2.8125, 1e-12, "predicted position");
    CHECK_NEAR(predicted.velocity_mps, -0.25, 1e-12, "predicted velocity");

    CHECK_EQ(sim_advance(nullptr, {1, 1}), SIM_INVALID_ARGUMENT, "advance rejects null");
    CHECK_EQ(sim_predict(nullptr, {1, 1}, &predicted), SIM_INVALID_ARGUMENT, "predict rejects null source");
    CHECK_EQ(sim_predict(&start, {1, 1}, nullptr), SIM_INVALID_ARGUMENT, "predict rejects null output");
}

TEST(sequential_ticks_integrate_from_the_previous_result) {
    SimState state{0.0, 0.0};
    CHECK_EQ(sim_advance(&state, {1200, 300}), SIM_OK, "first tick status");
    CHECK_EQ(sim_advance(&state, {-400, 700}), SIM_OK, "second tick status");
    CHECK_NEAR(state.position_m, 0.208, 1e-12, "sequential position");
    CHECK_NEAR(state.velocity_mps, 0.08, 1e-12, "sequential velocity");
}

int main() {
    RUN(subsecond_fractional_acceleration_moves_the_core);
    RUN(duration_and_acceleration_scaling_are_independent);
    RUN(signed_braking_uses_the_same_units);
    RUN(adapter_carries_wire_units_without_redefining_them);
    RUN(abi_advance_matches_the_direct_core);
    RUN(prediction_is_non_mutating_and_null_checks_survive);
    RUN(sequential_ticks_integrate_from_the_previous_result);
    return mt_summary();
}
