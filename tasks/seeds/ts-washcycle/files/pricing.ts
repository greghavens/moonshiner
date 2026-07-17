import type { PricingRule } from "./types.ts";
import { findPreset } from "./machines.ts";

export interface Load {
  kg: number;
  member: boolean;
  cycleCode: string;
}

// Base vend price in cents by cycle code.
const BASE_CENTS: Record<string, number> = {
  Q20: 350,
  N34: 450,
  H45: 600,
};

const memberDiscount: PricingRule<Load> = {
  name: "member-discount",
  applies: (load) => load.member,
  amount: () => -50,
};

const bulkyLoad: PricingRule<Load> = {
  name: "bulky-load",
  applies: (load) => load.kg > 9,
  amount: () => 100,
};

export const RULES = [memberDiscount, bulkyLoad];

// Vend price for one load: cycle base plus every applicable rule, never
// below zero (the till cannot pay customers).
export function priceLoad(load: Load): number {
  const spec = findPreset(load.cycleCode);
  let cents = BASE_CENTS[spec.code] ?? 0;
  for (const rule of RULES) {
    if (rule.applies(load)) {
      cents += rule.amount(load);
    }
  }
  return Math.max(0, cents);
}
