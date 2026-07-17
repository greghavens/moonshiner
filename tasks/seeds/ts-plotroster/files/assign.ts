import { Plot } from "./plot.ts";

export interface Applicant {
  name: string;
  minSqft: number;
}

// First-come first-served: each applicant gets the smallest open plot that
// meets their requested area (ties broken by plot id). Applicants we cannot
// seat go on the waitlist in application order.
export function assignPlots(
  plots: Plot[],
  applicants: Applicant[],
): { assigned: Map<string, string>; waitlist: string[] } {
  const open = [...plots].sort(
    (a, b) => a.sqft - b.sqft || a.id.localeCompare(b.id),
  );
  const assigned = new Map<string, string>();
  const waitlist: string[] = [];
  for (const app of applicants) {
    const idx = open.findIndex((p) => p.sqft >= app.minSqft);
    if (idx === -1) {
      waitlist.push(app.name);
      continue;
    }
    assigned.set(app.name, open[idx].id);
    open.splice(idx, 1);
  }
  return { assigned, waitlist };
}
