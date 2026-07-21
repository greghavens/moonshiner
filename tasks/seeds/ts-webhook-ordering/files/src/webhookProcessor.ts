export interface Webhook<T> {
  entityId: string;
  version: number;
  value: T;
}

export type Disposition =
  | "applied"
  | "buffered"
  | "duplicate"
  | "stale"
  | "dropped";

export interface ProcessResult {
  disposition: Disposition;
  appliedVersions: number[];
  currentVersion: number;
  pendingVersions: number[];
}

export interface EntitySnapshot<T> {
  version: number;
  value: T;
  pendingVersions: number[];
}

interface EntityRecord<T> {
  version: number;
  value?: T;
  pending: Map<number, Webhook<T>>;
}

/**
 * Applies versioned webhook payloads independently for each entity.
 *
 * Version zero is the implicit initial state. Events beyond the next expected
 * version are retained, while duplicate/stale events never replace state.
 * When the buffer is full, the nearest future versions are preferred because
 * they are the ones most likely to close the current gap.
 */
export class VersionedWebhookProcessor<T> {
  readonly #records = new Map<string, EntityRecord<T>>();
  readonly #maxPendingPerEntity: number;

  constructor(maxPendingPerEntity = 64) {
    if (!Number.isSafeInteger(maxPendingPerEntity) || maxPendingPerEntity < 1) {
      throw new RangeError("maxPendingPerEntity must be a positive safe integer");
    }
    this.#maxPendingPerEntity = maxPendingPerEntity;
  }

  process(event: Webhook<T>): ProcessResult {
    this.#validate(event);
    const record = this.#recordFor(event.entityId);

    if (event.version <= record.version) {
      return this.#result(
        record,
        event.version === record.version ? "duplicate" : "stale",
      );
    }

    if (record.pending.has(event.version)) {
      return this.#result(record, "duplicate");
    }

    if (event.version === record.version + 1) {
      this.#apply(record, event);
      const appliedVersions = [event.version, ...this.#drainPending(record)];
      return this.#result(record, "applied", appliedVersions);
    }

    record.pending.set(event.version, event);
    const retained = this.#enforceRetention(record, event.version);
    return this.#result(record, retained ? "buffered" : "dropped");
  }

  getState(entityId: string): EntitySnapshot<T> | undefined {
    const record = this.#records.get(entityId);
    if (record === undefined || record.version === 0) {
      return undefined;
    }

    return {
      version: record.version,
      value: record.value as T,
      pendingVersions: this.#pendingVersions(record),
    };
  }

  pendingCount(entityId: string): number {
    return this.#records.get(entityId)?.pending.size ?? 0;
  }

  #recordFor(entityId: string): EntityRecord<T> {
    let record = this.#records.get(entityId);
    if (record === undefined) {
      record = { version: 0, pending: new Map<number, Webhook<T>>() };
      this.#records.set(entityId, record);
    }
    return record;
  }

  #apply(record: EntityRecord<T>, event: Webhook<T>): void {
    record.version = event.version;
    record.value = event.value;
  }

  #drainPending(record: EntityRecord<T>): number[] {
    const appliedVersions: number[] = [];
    const nextVersion = record.version + 1;
    const next = record.pending.get(nextVersion);
    if (next === undefined) {
      return appliedVersions;
    }

    record.pending.delete(nextVersion);
    this.#apply(record, next);
    appliedVersions.push(nextVersion);
    return appliedVersions;
  }

  #enforceRetention(record: EntityRecord<T>, incomingVersion: number): boolean {
    if (record.pending.size <= this.#maxPendingPerEntity) {
      return true;
    }

    const farthestVersion = Math.max(...record.pending.keys());
    record.pending.delete(farthestVersion);
    return farthestVersion !== incomingVersion;
  }

  #result(
    record: EntityRecord<T>,
    disposition: Disposition,
    appliedVersions: number[] = [],
  ): ProcessResult {
    return {
      disposition,
      appliedVersions,
      currentVersion: record.version,
      pendingVersions: this.#pendingVersions(record),
    };
  }

  #pendingVersions(record: EntityRecord<T>): number[] {
    return [...record.pending.keys()].sort((left, right) => left - right);
  }

  #validate(event: Webhook<T>): void {
    if (event.entityId.length === 0) {
      throw new TypeError("entityId must not be empty");
    }
    if (!Number.isSafeInteger(event.version) || event.version < 1) {
      throw new RangeError("version must be a positive safe integer");
    }
  }
}
