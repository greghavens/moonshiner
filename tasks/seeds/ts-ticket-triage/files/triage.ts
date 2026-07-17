export type Ticket = {
  id: number;
  subject: string;
  slaMinutes: number; // response-time budget promised for this ticket's plan
  openedAt: number; // epoch ms
};

/** Minutes remaining until this ticket breaches its SLA (negative = breached). */
export function minutesLeft(ticket: Ticket, now: number): number {
  const elapsed = Math.floor((now - ticket.openedAt) / 60_000);
  return ticket.slaMinutes - elapsed;
}

/**
 * Minutes-from-now at which each open ticket breaches, soonest first.
 * Drives the countdown strip at the top of the dashboard.
 */
export function breachTimeline(tickets: Ticket[], now: number): number[] {
  const offsets = tickets.map((t) => minutesLeft(t, now));
  return offsets.sort();
}

/** The single ticket an agent should pick up next. */
export function nextDue(tickets: Ticket[], now: number): Ticket | undefined {
  let best: Ticket | undefined;
  for (const t of tickets) {
    if (!best || minutesLeft(t, now) < minutesLeft(best, now)) {
      best = t;
    }
  }
  return best;
}

/** Ticket ids for the daily digest email, ascending. */
export function digestIds(tickets: Ticket[]): number[] {
  return tickets.map((t) => t.id).sort();
}

/** One-line summary per ticket, in pick-up order. */
export function queueSummary(tickets: Ticket[], now: number): string[] {
  const remaining = new Map(tickets.map((t) => [t, minutesLeft(t, now)]));
  return [...tickets]
    .sort((a, b) => (remaining.get(a) ?? 0) - (remaining.get(b) ?? 0))
    .map((t) => `#${t.id} ${t.subject} (${remaining.get(t)}m left)`);
}
