export interface ResourceSnapshot<T> {
  version: number;
  value: T;
}

export interface ResourceIdentity {
  tenantId: string;
  resourceId: string;
}

export interface WriteRequestTrace extends ResourceIdentity {
  traceId: string;
  operation: "write";
  version: number;
}

export interface InvalidationEvent {
  eventId: string;
  causedByTraceId: string;
  version: number;
}

export type ResourceLoader<T> = (
  identity: ResourceIdentity,
) => Promise<ResourceSnapshot<T>>;

function cacheKey(identity: ResourceIdentity): string {
  return JSON.stringify([identity.tenantId, identity.resourceId]);
}

export class VersionedTenantCache<T> {
  readonly #load: ResourceLoader<T>;
  readonly #entries = new Map<string, ResourceSnapshot<T>>();
  readonly #writeTraces = new Map<string, WriteRequestTrace>();

  constructor(load: ResourceLoader<T>) {
    this.#load = load;
  }

  async read(identity: ResourceIdentity): Promise<ResourceSnapshot<T>> {
    const key = cacheKey(identity);
    const cached = this.#entries.get(key);
    if (cached !== undefined) {
      return cached;
    }

    const loaded = await this.#load(identity);
    this.#entries.set(key, loaded);
    return loaded;
  }

  recordRequestTrace(trace: WriteRequestTrace): void {
    this.#writeTraces.set(trace.traceId, trace);
  }

  handleInvalidation(event: InvalidationEvent): boolean {
    const trace = this.#writeTraces.get(event.eventId);
    if (trace === undefined || trace.version !== event.version) {
      return false;
    }

    const key = cacheKey(trace);
    const cached = this.#entries.get(key);
    if (cached === undefined || cached.version > event.version) {
      return false;
    }

    this.#entries.delete(key);
    this.#writeTraces.delete(trace.traceId);
    return true;
  }
}
