export interface SelectorProfile {
  readonly lookups: number;
  readonly scanned: number;
}

interface Entry<T, K> {
  value: T;
  key: K;
}

function sameMapKey<K>(left: K, right: K): boolean {
  return left === right || (left !== left && right !== right);
}

/**
 * A small mutable collection optimized for repeated equality selectors.
 *
 * Values are matched by identity for update/delete. If the same value was
 * inserted more than once, those operations affect the first occurrence.
 */
export class IndexedSelector<T, K> {
  readonly #entries: Entry<T, K>[] = [];
  readonly #index = new Map<K, Entry<T, K>[]>();
  readonly #selector: (value: T) => K;
  #lookups = 0;
  #scanned = 0;

  constructor(values: readonly T[], selector: (value: T) => K) {
    this.#selector = selector;

    for (const value of values) {
      const entry = { value, key: selector(value) };
      this.#entries.push(entry);
      this.#appendToIndex(entry);
    }
  }

  get size(): number {
    return this.#entries.length;
  }

  add(value: T): void {
    const entry = { value, key: this.#selector(value) };
    this.#entries.push(entry);
    this.#appendToIndex(entry);
  }

  update(previous: T, next: T): boolean {
    const position = this.#entries.findIndex((entry) => Object.is(entry.value, previous));
    if (position === -1) {
      return false;
    }

    const entry = this.#entries[position];
    const nextKey = this.#selector(next);

    if (!sameMapKey(entry.key, nextKey)) {
      this.#removeFromIndex(entry);
      entry.key = nextKey;
      entry.value = next;
      this.#insertIntoIndex(entry, position);
    } else {
      entry.key = nextKey;
      entry.value = next;
    }

    return true;
  }

  delete(value: T): boolean {
    const position = this.#entries.findIndex((entry) => Object.is(entry.value, value));
    if (position === -1) {
      return false;
    }

    const [entry] = this.#entries.splice(position, 1);
    this.#removeFromIndex(entry);
    return true;
  }

  select(key: K): readonly T[] {
    this.#lookups += 1;

    const bucket = this.#index.get(key) ?? [];
    const candidates = new Set(bucket);
    const selected: T[] = [];

    // Keep collection ordering authoritative while checking the indexed
    // candidates. This also makes profiling reflect the work done here.
    for (const entry of this.#entries) {
      this.#scanned += 1;
      if (candidates.has(entry)) {
        selected.push(entry.value);
      }
    }

    return selected;
  }

  profile(): SelectorProfile {
    return { lookups: this.#lookups, scanned: this.#scanned };
  }

  resetProfile(): void {
    this.#lookups = 0;
    this.#scanned = 0;
  }

  snapshot(): readonly T[] {
    return this.#entries.map((entry) => entry.value);
  }

  #appendToIndex(entry: Entry<T, K>): void {
    const bucket = this.#index.get(entry.key);
    if (bucket === undefined) {
      this.#index.set(entry.key, [entry]);
    } else {
      bucket.push(entry);
    }
  }

  #insertIntoIndex(entry: Entry<T, K>, entryPosition: number): void {
    let bucket = this.#index.get(entry.key);
    if (bucket === undefined) {
      bucket = [];
      this.#index.set(entry.key, bucket);
    }

    let bucketPosition = 0;
    for (let position = 0; position < entryPosition; position += 1) {
      if (sameMapKey(this.#entries[position].key, entry.key)) {
        bucketPosition += 1;
      }
    }
    bucket.splice(bucketPosition, 0, entry);
  }

  #removeFromIndex(entry: Entry<T, K>): void {
    const bucket = this.#index.get(entry.key);
    if (bucket === undefined) {
      return;
    }

    const position = bucket.indexOf(entry);
    if (position !== -1) {
      bucket.splice(position, 1);
    }
    if (bucket.length === 0) {
      this.#index.delete(entry.key);
    }
  }
}
