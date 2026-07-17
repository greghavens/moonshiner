import { zoneOf } from "./plot";
import type { Plot } from "./plot.ts";

// Season fee: $40 base plus a quarter per square foot; raised beds add $15
// for the lumber fund; creek-zone beds get $10 off for putting up with the
// spring flooding.
export function seasonFee(plot: Plot): number {
  let fee = 40 + 0.25 * plot.sqft;
  if (plot.raised) {
    fee += 15;
  }
  if (zoneOf(plot.bed) === "creek") {
    fee -= 10;
  }
  return fee;
}

export function formatFee(dollars: number): string {
  return `$${dollars.toFixed(2)}`;
}
