import { DockState, type Station } from "./stations.ts";

// Every active dock aims to sit at half its racks, rounded up; the deficit
// is how many bikes the van should drop off (negative means pick up).
// Docks with no field report yet, and docks not in active service, are
// left out of the plan entirely.
export function deficitOf(st: Station): number {
  if (st.state !== DockState.Active || !st.lastReport) {
    return 0;
  }
  const target = Math.ceil(st.capacity / 2);
  return target - st.lastReport.bikes;
}

// A dock is over capacity when the crew reports more bikes on the ground
// than racks (overflow parking) — those need a pickup regardless of state.
export function overCapacity(st: Station): boolean {
  return st.lastReport?.bikes > st.capacity;
}

// Docks the van may service: anything except out-of-service docks and the
// reserve pool — ops staffs reserve docks by hand for event surges.
export function pullCandidates(stations: Station[]): Station[] {
  return stations.filter(
    (s) => s.state !== DockState.Offline && s.state !== DockState.Standby,
  );
}

// Total bikes the van has to carry for a route: the sum of the positive
// deficits. Rows are whatever the report layer built, as long as each one
// carries its computed deficit.
export function totalShortfall<T>(rows: T[]): number {
  let sum = 0;
  for (const row of rows) {
    sum += Math.max(0, row.deficit);
  }
  return sum;
}
