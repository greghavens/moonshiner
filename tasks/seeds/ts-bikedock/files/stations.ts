// Dock-state vocabulary for the station network. Kept as a const object so
// the wire strings and the in-code names live in one place.
export const DockState = {
  Active: "active",
  Reserve: "reserve",
  Offline: "offline",
} as const;

export interface Report {
  bikes: number;
  broken: number;
}

export interface Station {
  id: string;
  name: string;
  capacity: number;
  state: string;
  lastReport?: Report;
}

const STATE_LABELS: Record<string, string> = {
  active: "in service",
  reserve: "reserve pool",
  offline: "out of service",
};

export function describeState(state: DockState): string {
  return STATE_LABELS[state] ?? "unknown";
}
