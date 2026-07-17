import type { MachineDriver } from "./types.ts";
import { findPreset } from "./machines.ts";

export interface QueuedLoad {
  machine: string;
  cycleCode: string;
}

// Kicks off every queued load whose machine has a live driver, in queue
// order, and returns the cycle codes actually started. Loads pointing at a
// machine we have no driver for stay in the queue for the next sweep.
export function startQueue(
  queue: QueuedLoad[],
  drivers: Map<string, MachineDriver>,
): string[] {
  const started: string[] = [];
  for (const q of queue) {
    const driver = drivers.get(q.machine);
    if (!driver) {
      continue;
    }
    const spec = findPreset(q.cycleCode);
    driver.lockDoor();
    driver.start(spec.code);
    started.push(spec.code);
  }
  return started;
}
