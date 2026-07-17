// Cycle and pricing contracts, updated for the C-40 controller rollout.
// This file is the signed-off contract — the rest of the code adapts to it.

export type Temp = "cold" | "warm" | "hot";

export interface CycleSpec {
  code: string;
  label: string;
  // Runtime in whole minutes. The C-40 rollout renamed this from the old
  // durationMin field when the sheet formats were unified.
  minutes: number;
  temp: Temp;
}

export interface MachineDriver {
  // C-40 controllers need the runtime up front; older firmware inferred it
  // from the cycle code table.
  start(code: string, minutes: number): void;
  lockDoor(): void;
}

// Pricing rules now declare what they contribute for a load, in cents
// (negative for discounts). The result type is a parameter so the loyalty
// team can experiment with non-cent outcomes without touching this file.
export interface PricingRule<L, R> {
  name: string;
  applies(load: L): boolean;
  amount(load: L): R;
}
