#pragma once

#include "simulation_bindings.h"
#include "simulation_core.hpp"

namespace simadapter {

inline simcore::State to_core_state(const SimState &state) {
    return {state.position_m, state.velocity_mps};
}

inline simcore::StepCommand to_core_tick(const SimTick &tick) {
    return {tick.acceleration_mmps2, tick.elapsed_ms};
}

inline SimState from_core_state(const simcore::State &state) {
    return {state.position_m, state.velocity_mps};
}

} // namespace simadapter
