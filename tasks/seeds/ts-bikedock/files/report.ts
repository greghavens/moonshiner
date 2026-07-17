import type { Station } from "./stations.ts";
import { deficitOf, totalShortfall } from "./rebalance.ts";

const shortLabel = (s) => `${s.name} [${s.id}]`;

function bikesWord(n) {
  return n === 1 ? "1 bike" : `${n} bikes`;
}

// Worst deficit first; ties keep the dispatcher's original ordering. The
// incoming list is the dispatcher's live snapshot — never reorder it in
// place, they are staring at it on the wall board.
export function rankStations(stations: readonly Station[]): Station[] {
  return stations.sort((a: Station, b: Station) => deficitOf(b) - deficitOf(a));
}

// One line per station that actually needs a drop-off, ranked.
export function summaryLines(stations: readonly Station[]): string[] {
  return rankStations(stations)
    .filter((s) => deficitOf(s) > 0)
    .map((s) => `${shortLabel(s)} needs ${bikesWord(deficitOf(s))}`);
}

// How many bikes the van must load at the depot to cover the whole route.
export function vanLoad(stations: readonly Station[]): number {
  const rows = stations.map((s) => ({ station: s, deficit: deficitOf(s) }));
  return totalShortfall(rows);
}
