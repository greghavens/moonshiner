// Scheduling helpers for the field-service portal.
//
// Instants move through the system as UTC ISO strings ("...Z"); sites carry
// an IANA timezone and all "what day/week is it for the site" questions are
// answered in that zone via TZDate.
import { TZDate } from "@date-fns/tz";
import { addDays, format, isSameDay } from "date-fns";

/** Wall-clock rendering of a UTC instant at the site, "yyyy-MM-dd HH:mm". */
export function fmtLocal(instantIso, tz) {
  return format(new TZDate(instantIso, tz), "yyyy-MM-dd HH:mm");
}

/** True when two UTC instants land on the same calendar day at the site. */
export function isSameLocalDay(aIso, bIso, tz) {
  return isSameDay(new TZDate(aIso, tz), new TZDate(bIso, tz));
}

/** Next visit due date: cadence in whole days from the last visit instant. */
export function nextVisitDue(lastVisitIso, cadenceDays) {
  if (!Number.isInteger(cadenceDays) || cadenceDays <= 0) {
    throw new Error("cadenceDays must be a positive integer");
  }
  return addDays(new Date(lastVisitIso), cadenceDays).toISOString();
}
