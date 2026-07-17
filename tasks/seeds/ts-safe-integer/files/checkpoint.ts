// Checkpoint tracking for the order-events consumer. The broker publishes one
// JSON object per line; event ids are unsigned decimal integers that strictly
// increase per partition. We persist the highest processed id and use it to
// drop replays after a reconnect, so id fidelity is the whole ballgame here.

export interface StreamEvent {
  id: bigint;
  kind: string;
}

export function parseEvent(line: string): StreamEvent {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    throw new Error('malformed event: not valid JSON');
  }
  if (typeof raw !== 'object' || raw === null) {
    throw new Error('malformed event: expected an object');
  }
  const { id, kind } = raw as { id?: unknown; kind?: unknown };
  if (typeof kind !== 'string' || kind.length === 0) {
    throw new Error('malformed event: missing kind');
  }
  if (typeof id !== 'number' || !Number.isFinite(id)) {
    throw new Error('invalid event id: not a number');
  }
  if (!Number.isInteger(id) || id < 0) {
    throw new Error(`invalid event id: ${id}`);
  }
  return { id: BigInt(id), kind };
}

export function compareIds(a: bigint, b: bigint): -1 | 0 | 1 {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

export function nextId(id: bigint): bigint {
  return id + 1n;
}

export class CheckpointStore {
  lastId: bigint | null = null;

  // True when the event advances the checkpoint; false means the id is
  // already covered (a replay) and the caller must skip the event.
  advance(id: bigint): boolean {
    if (this.lastId !== null && compareIds(id, this.lastId) <= 0) {
      return false;
    }
    this.lastId = id;
    return true;
  }

  // Wire format shared with the dashboard: lastId is a bare JSON number so
  // downstream tooling can diff checkpoints without a schema change.
  serialize(): string {
    if (this.lastId === null) {
      return '{"lastId":null}';
    }
    return JSON.stringify({ lastId: Number(this.lastId) });
  }
}
