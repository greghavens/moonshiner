// Maintenance-window planning for our Datadog downtime tooling.
//
// A MaintenanceWindow is the internal, vendor-neutral description of a
// planned quiet period. The Datadog v2 downtime API has two schedule
// families with different datetime rules:
//   - one-time schedules use ISO-8601 datetimes WITH a zero UTC offset
//     (e.g. 2026-08-01T02:00:00+00:00)
//   - recurring schedules use an RRULE plus a local start WITHOUT any
//     UTC offset (e.g. 2026-08-01T02:00:00) and a duration like "2h"
// validateWindow enforces those rules up front so bad windows never reach
// the API.

export type MaintenanceWindow = {
  name: string;
  scope: string;
  monitorId?: number;
  monitorTags?: string[];
  start?: string;
  end?: string;
  rrule?: string;
  duration?: string;
  timezone?: string;
  message?: string;
  muteFirstRecovery?: boolean;
};

const ZERO_OFFSET = /(?:\+00:00|Z)$/;
const LOCAL_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/;
const DURATION = /^\d+[mhdw]$/;
const WEEKDAYS = new Set(["MO", "TU", "WE", "TH", "FR", "SA", "SU"]);

export function validateWindow(w: MaintenanceWindow): string[] {
  const problems: string[] = [];
  if (!w.name) problems.push("name is required");
  if (!w.scope) problems.push("scope is required");
  const hasId = typeof w.monitorId === "number";
  const hasTags = Array.isArray(w.monitorTags) && w.monitorTags.length > 0;
  if (hasId === hasTags) {
    problems.push("exactly one of monitorId or monitorTags is required");
  }
  if (w.rrule) {
    if (!w.duration || !DURATION.test(w.duration)) {
      problems.push("recurring windows need a duration like 2h");
    }
    if (w.start && !LOCAL_DATETIME.test(w.start)) {
      problems.push("recurring start must not include a UTC offset");
    }
    if (w.end) {
      problems.push("recurring windows use duration, not end");
    }
  } else {
    if (w.start && !ZERO_OFFSET.test(w.start)) {
      problems.push("one-time start must include a zero UTC offset");
    }
    if (w.end && !ZERO_OFFSET.test(w.end)) {
      problems.push("one-time end must include a zero UTC offset");
    }
    if (
      w.start &&
      w.end &&
      Date.parse(w.end) <= Date.parse(w.start)
    ) {
      problems.push("end must be after start");
    }
  }
  return problems;
}

export function planWeeklyWindow(opts: {
  name: string;
  scope: string;
  monitorId?: number;
  monitorTags?: string[];
  day: string;
  firstDate: string;
  startTime: string;
  duration: string;
  timezone?: string;
  message?: string;
}): MaintenanceWindow {
  if (!WEEKDAYS.has(opts.day)) {
    throw new Error(`unknown RRULE weekday: ${opts.day}`);
  }
  const window: MaintenanceWindow = {
    name: opts.name,
    scope: opts.scope,
    rrule: `FREQ=WEEKLY;INTERVAL=1;BYDAY=${opts.day}`,
    start: `${opts.firstDate}T${opts.startTime}`,
    duration: opts.duration,
  };
  if (typeof opts.monitorId === "number") window.monitorId = opts.monitorId;
  if (opts.monitorTags) window.monitorTags = opts.monitorTags;
  if (opts.timezone) window.timezone = opts.timezone;
  if (opts.message) window.message = opts.message;
  return window;
}
