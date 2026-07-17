import type { CycleSpec } from "./types.ts";

export const PRESETS: CycleSpec[] = [
  { code: "Q20", label: "Quick wash", minutes: 20, temp: "cold" },
  { code: "N34", label: "Normal", minutes: 34, temp: "warm" },
  { code: "H45", label: "Heavy duty", minutes: 45, temp: "hot" },
];

export function findPreset(code: string): CycleSpec {
  const spec = PRESETS.find((p) => p.code === code);
  if (!spec) {
    throw new Error(`unknown cycle code: ${code}`);
  }
  return spec;
}

// Wall-clock finish time as minutes since midnight, wrapping past midnight
// for the late crowd.
export function estimateEnd(spec: CycleSpec, startMin: number): number {
  return (startMin + spec.durationMin) % 1440;
}
