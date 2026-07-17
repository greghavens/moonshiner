// Civil-date helpers for the tenant-facing daily shipment report. A tenant
// picks a report day like "2026-03-08"; a Zone adapter supplies the
// tenant's UTC offset in minutes east of UTC for any given instant, so all
// day-boundary math must go through the adapter.

export interface Zone {
  offsetMinutes(utcMs: number): number;
}

export interface ShipmentEvent {
  at: number; // epoch milliseconds, UTC
  qty: number;
}

export function startOfCivilDay(date: string, zone: Zone): number {
  const wall = Date.parse(date);
  return wall + zone.offsetMinutes(wall) * 60_000;
}

export function civilDateOf(utcMs: number, zone: Zone): string {
  const shifted = utcMs + zone.offsetMinutes(utcMs) * 60_000;
  return new Date(shifted).toISOString().slice(0, 10);
}

export function nextCivilDate(date: string): string {
  return new Date(Date.parse(date) + 86_400_000).toISOString().slice(0, 10);
}

export function civilDayHours(date: string, zone: Zone): number {
  return (startOfCivilDay(nextCivilDate(date), zone) - startOfCivilDay(date, zone)) / 3_600_000;
}

export function dailyTotals(events: ShipmentEvent[], zone: Zone): Record<string, number> {
  const totals: Record<string, number> = {};
  for (const event of events) {
    const day = new Date(event.at).toISOString().slice(0, 10);
    totals[day] = (totals[day] ?? 0) + event.qty;
  }
  return totals;
}
