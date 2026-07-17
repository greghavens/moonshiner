// Rollout gate for fleet telemetry agents.
//
// An agent build qualifies for the next ring when its version sits inside
// the policy's supported range and its last heartbeat is fresh enough.
// Staleness windows are written the way ops writes them ("36h", "2w").
import ms from "ms";
import semver from "semver";

/** Parse an ops staleness window ("45m", "36h", "2w") into milliseconds. */
export function parseWindow(window) {
  let out;
  try {
    out = ms(String(window));
  } catch {
    out = undefined;
  }
  if (typeof out !== "number" || Number.isNaN(out) || out <= 0) {
    throw new Error(`unrecognized window: ${window}`);
  }
  return out;
}

/** Lowest agent version a policy still accepts, as a plain string. */
export function minSupported(policy) {
  const min = semver.minVersion(policy.supported);
  if (min === null) {
    throw new Error(`unusable supported range: ${policy.supported}`);
  }
  return min.version;
}

/**
 * Gate one agent against a rollout policy.
 * agent:  { id, version, lastSeen }   policy: { supported, maxStaleness }
 * Returns { ok, reasons } — reasons list every failed check.
 */
export function qualifies(agent, policy, nowIso) {
  const reasons = [];
  if (!semver.valid(agent.version)) {
    reasons.push(`unparseable version ${agent.version}`);
  } else if (!semver.satisfies(agent.version, policy.supported)) {
    reasons.push(`version ${agent.version} outside ${policy.supported}`);
  }
  const age = Date.parse(nowIso) - Date.parse(agent.lastSeen);
  if (age > parseWindow(policy.maxStaleness)) {
    reasons.push(`last seen ${agent.lastSeen} is older than ${policy.maxStaleness}`);
  }
  return { ok: reasons.length === 0, reasons };
}

/** First ring whose range accepts the version, or null. */
export function ringFor(version, rings) {
  for (const ring of rings) {
    if (semver.satisfies(version, ring.range)) {
      return ring.name;
    }
  }
  return null;
}
