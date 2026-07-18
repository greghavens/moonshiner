#ifndef SIMULATION_BINDINGS_H
#define SIMULATION_BINDINGS_H

/* Generated controller ABI. Numeric input units are part of the wire contract. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct SimState {
    double position_m;
    double velocity_mps;
} SimState;

typedef struct SimTick {
    int32_t acceleration_mmps2;
    uint32_t elapsed_ms;
} SimTick;

enum SimStatus {
    SIM_OK = 0,
    SIM_INVALID_ARGUMENT = 22
};

int sim_advance(SimState *state, SimTick tick);
int sim_predict(const SimState *state, SimTick tick, SimState *predicted);

#ifdef __cplusplus
}
#endif

#endif
