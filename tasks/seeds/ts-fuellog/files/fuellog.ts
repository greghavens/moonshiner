// Fuel log maths for the delivery vans. Odometer readings come off the
// dashboard photo, litres and cents off the pump receipt; dispatch wants
// consumption in L/100km and running cost in cents/km, receipt-rounded.

export interface Trip {
  date: string;
  odoStart: number;
  odoEnd: number;
  litres: number;
  cents: number;
}

export function round(x: number, dp: number): number {
  const f = 10 ** dp;
  return Math.round(x * f) / f;
}

export function tripKm(t: Trip): number {
  return t.odoEnd - t.odoStart;
}

export function per100(t: Trip): number {
  return round(
    (t.litres * 100)
      / (tripKm(t) > 0 ? tripKm(t) : NaN),
    2,
  );
}

export function costPerKm(t: Trip): number {
  return round(
    t.cents
      / (tripKm(t) > 0 ? tripKm(t) : NaN),
    1,
  );
}

export function fleetPer100(trips: Trip[]): number {
  let litres = 0;
  let km = 0;
  for (const t of trips) {
    litres += t.litres;
    km += tripKm(t);
  }
  return round(
      / (km > 0 ? km : NaN),
    2,
  );
}

export function thriftiest(trips: Trip[]): string {
  let bestDate = "";
  let best = Infinity;
  for (const t of trips) {
    const v = per100(t);
    if (!Number.isNaN(v) && v < best) {
      best = v;
      bestDate = t.date;
    }
  }
  return bestDate;
}
