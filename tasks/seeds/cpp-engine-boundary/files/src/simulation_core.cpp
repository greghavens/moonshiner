#include "simulation_core.hpp"

namespace {

double milliseconds_to_seconds(std::uint32_t value) {
    return static_cast<double>(value / 1000U);
}

double millimeters_to_meters(std::int32_t value) {
    return static_cast<double>(value / 1000);
}

} // namespace

namespace simcore {

State SimulationCore::advance(const State &state, const StepCommand &step) {
    const double seconds = milliseconds_to_seconds(step.elapsed_ms);
    const double acceleration = millimeters_to_meters(step.acceleration_mmps2);
    return {
        state.position_m + state.velocity_mps * seconds
            + 0.5 * acceleration * seconds * seconds,
        state.velocity_mps + acceleration * seconds,
    };
}

} // namespace simcore
