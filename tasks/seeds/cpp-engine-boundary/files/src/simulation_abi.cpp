#include "simulation_bindings.h"
#include "simulation_adapter.hpp"

extern "C" int sim_advance(SimState *state, SimTick tick) {
    if (state == nullptr) {
        return SIM_INVALID_ARGUMENT;
    }
    const simcore::State next = simcore::SimulationCore::advance(
        simadapter::to_core_state(*state), simadapter::to_core_tick(tick));
    *state = simadapter::from_core_state(next);
    return SIM_OK;
}

extern "C" int sim_predict(const SimState *state, SimTick tick, SimState *predicted) {
    if (state == nullptr || predicted == nullptr) {
        return SIM_INVALID_ARGUMENT;
    }
    const simcore::State next = simcore::SimulationCore::advance(
        simadapter::to_core_state(*state), simadapter::to_core_tick(tick));
    *predicted = simadapter::from_core_state(next);
    return SIM_OK;
}
