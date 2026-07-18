import { normalizeStockAdjusted } from '../../packages/compat/normalize_stock_event.ts';

export interface InventoryRow {
  quantity: number;
  sold: number;
  returned: number;
  recounts: number;
  lastEventId: string | null;
}

function key(warehouseId: string, sku: string): string {
  return `${warehouseId}\u0000${sku}`;
}

export class InventoryProjection {
  private readonly rows = new Map<string, InventoryRow>();

  seed(warehouseId: string, sku: string, quantity: number): void {
    this.rows.set(key(warehouseId, sku), {
      quantity, sold: 0, returned: 0, recounts: 0, lastEventId: null,
    });
  }

  apply(input: unknown): void {
    const event = normalizeStockAdjusted(input);
    const rowKey = key(event.payload.warehouseId, event.payload.sku);
    const previous = this.rows.get(rowKey) ?? {
      quantity: 0, sold: 0, returned: 0, recounts: 0, lastEventId: null,
    };
    const next: InventoryRow = {
      ...previous,
      quantity: previous.quantity + event.payload.delta,
      lastEventId: event.eventId,
    };
    switch (event.payload.reason) {
      case 'sale':
        next.sold += Math.abs(event.payload.delta);
        break;
      case 'return':
        next.returned += Math.abs(event.payload.delta);
        break;
      case 'recount':
        next.recounts += 1;
        break;
      default:
        throw new TypeError(`unsupported adjustment reason ${event.payload.reason}`);
    }
    this.rows.set(rowKey, next);
  }

  get(warehouseId: string, sku: string): InventoryRow | undefined {
    const row = this.rows.get(key(warehouseId, sku));
    return row === undefined ? undefined : { ...row };
  }
}
