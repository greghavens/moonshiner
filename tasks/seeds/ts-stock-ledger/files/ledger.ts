export type Movement = { sku: string; delta: number };

/** In-memory stand-in for the warehouse DB client (one round trip per call). */
class StockDb {
  private rows: Map<string, number>;

  constructor() {
    this.rows = new Map();
  }

  async get(sku: string): Promise<number> {
    await Promise.resolve();
    return this.rows.get(sku) ?? 0;
  }

  async put(sku: string, qty: number): Promise<void> {
    await Promise.resolve();
    this.rows.set(sku, qty);
  }
}

export class StockLedger {
  private db: StockDb;
  private movements: Movement[];

  constructor() {
    this.db = new StockDb();
    this.movements = [];
  }

  /** Apply one stock movement and return the new on-hand quantity. */
  async adjust(sku: string, delta: number): Promise<number> {
    const current = await this.db.get(sku);
    const next = current + delta;
    if (next < 0) throw new Error(`stock for ${sku} would go negative`);
    await this.db.put(sku, next);
    this.movements.push({ sku, delta });
    return next;
  }

  /** Apply every line of a purchase order or cycle count, in order. */
  async applyBatch(movements: Movement[]): Promise<void> {
    for (const movement of movements) {
      this.adjust(movement.sku, movement.delta);
    }
  }

  async quantity(sku: string): Promise<number> {
    return this.db.get(sku);
  }

  /** Audit trail of applied movements, oldest first. */
  history(): Movement[] {
    return [...this.movements];
  }
}
