import type { CycleSpec } from "./types.ts";

// Attendant sheets arrive as pipe rows: "code|label|minutes|temp".
// Bad rows must be rejected loudly — a silent skip once cost us a week of
// mispriced delicates.
export function parseCycleRow(row: string): CycleSpec {
  const parts = row.split("|");
  if (parts.length !== 4) {
    throw new Error(`bad cycle row: ${row}`);
  }
  const [code, label, minutesText, temp] = parts;
  if (!code || !label) {
    throw new Error(`bad cycle row: ${row}`);
  }
  const minutes = Number(minutesText);
  if (!Number.isInteger(minutes) || minutes <= 0) {
    throw new Error(`bad cycle row: ${row}`);
  }
  return { code, label, minutes, temp };
}

// One receipt line per finished load, e.g. "N34 Normal — 34 min, warm".
export function receiptLine(spec: CycleSpec): string {
  return `${spec.code} ${spec.label} — ${spec.minutes} min, ${spec.temp}`;
}
