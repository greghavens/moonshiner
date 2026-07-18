#pragma once

#include <cstdint>

namespace simcore {

struct State {
    double position_m;
    double velocity_mps;
};

struct StepCommand {
    std::int32_t acceleration_mmps2;
    std::uint32_t elapsed_ms;
};

class SimulationCore {
public:
    static State advance(const State &state, const StepCommand &step);
};

} // namespace simcore
