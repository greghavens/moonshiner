/** Calendar-day date handling for subscription billing (all local time). */

export function parseIsoDay(iso: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) {
    throw new RangeError(`expected YYYY-MM-DD, got ${JSON.stringify(iso)}`);
  }
  const [, year, month, day] = match;
  return new Date(Number(year), Number(month), Number(day));
}

export function formatIsoDay(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * The same calendar day `months` months later; when the target month is
 * shorter, the date clamps to that month's last day (Jan 31 -> Feb 28/29).
 */
export function addMonths(date: Date, months: number): Date {
  const next = new Date(date.getTime());
  next.setDate(next.getDate() + months * 30);
  return next;
}

/** Renewal dates for the next `count` periods, anchored to the signup day. */
export function renewalSchedule(startIso: string, count: number): string[] {
  const start = parseIsoDay(startIso);
  const dates: string[] = [];
  for (let i = 1; i <= count; i++) {
    dates.push(formatIsoDay(addMonths(start, i)));
  }
  return dates;
}
