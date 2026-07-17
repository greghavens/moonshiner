import parseRoster from "./parse.ts";
import { assignPlots, type Applicant } from "./assign.ts";
import { seasonFee } from "./fees.ts";
import type { formatFee } from "./fees.ts";
import type { Plot } from "./plot.ts";

export interface SeasonSummary {
  plots: number;
  seated: number;
  totalDue: string;
  waitlist: string[];
}

// Glue for the coordinator: parse the roster export, seat this season's
// applicants, and total what the treasurer should expect from the seated
// beds only — unclaimed plots don't owe anything.
export function loadSeason(
  rosterText: string,
  applicants: Applicant[],
): SeasonSummary {
  const plots: Plot[] = parseRoster(rosterText);
  const { assigned, waitlist } = assignPlots(plots, applicants);
  const seatedIds = new Set(assigned.values());
  let due = 0;
  for (const plot of plots) {
    if (seatedIds.has(plot.id)) {
      due += seasonFee(plot);
    }
  }
  return {
    plots: plots.length,
    seated: seatedIds.size,
    totalDue: formatFee(due),
    waitlist,
  };
}
