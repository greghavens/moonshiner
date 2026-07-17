// Parcel-locker door states: what the kiosk shows for each door, what an
// event does to it, and what the courier account gets billed.
//
// States flow: empty -> reserved -> loaded -> (picked up | overdue), with
// jammed reachable from anywhere a courier can report a stuck door.

export type DoorEvent =
  | "reserve"
  | "load"
  | "pickup"
  | "expire"
  | "cancel"
  | "report-jam"
  | "service";

export function doorLabel(state: string): string {
  switch (state) {
    case "empty":
      return "Available";
    case "reserved":
      return "Reserved";
    case "loaded":
      return "Parcel inside";
    case "overdue":
      return "Awaiting return pickup";
    case "jammed":
      return "Out of service";
  }
}

export function nextState(state: string, event: DoorEvent): string {
  switch (state) {
    case "empty":
      if (event === "reserve") {
        return "reserved";
      }
      return "empty";
    case "reserved":
      if (event === "load") {
        return "loaded";
      }
      if (event === "cancel") {
        return "empty";
      }
      return "reserved";
    case "overdue":
      if (event === "service") {
        return "empty";
      }
    case "loaded":
      if (event === "pickup") {
        return "empty";
      }
      if (event === "expire") {
        return "overdue";
      }
      if (event === "report-jam") {
        return "jammed";
      }
      return state;
    case "jammed":
      if (event === "service") {
        return "empty";
      }
      return "jammed";
  }
}

export function storageFee(state: string, daysHeld: number): number {
  let fee = 0;
  switch (state) {
    case "overdue":
      fee += 50 * Math.max(0, daysHeld - 3);
    case "loaded":
      fee += 25;
      return fee;
    case "empty":
    case "reserved":
      return 0;
    case "jammed":
      return 0;
  }
}

export function slaBucket(minutesWaiting: number): string {
  if (minutesWaiting < 60) {
    return "fresh";
  } else if (minutesWaiting < 24 * 60) {
    return "same-day";
  } else if (minutesWaiting < 72 * 60) {
    return "aging";
  } else if (minutesWaiting >= 72 * 60) {
    return "stale";
  }
}
