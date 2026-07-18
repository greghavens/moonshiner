// Generated from stock-adjusted-v2.schema.json. DO NOT EDIT BY HAND.
export type AdjustmentReason = 'sale' | 'return' | 'recount';

export interface StockAdjustedV2 {
  schemaVersion: 2;
  type: 'inventory.stock_adjusted';
  eventId: string;
  occurredAt: string;
  payload: {
    warehouseId: string;
    sku: string;
    delta: number;
    reason: AdjustmentReason;
    actorId: string | null;
  };
}

const REASONS = new Set<AdjustmentReason>(['sale', 'return', 'recount']);

export function isStockAdjustedV2(value: unknown): value is StockAdjustedV2 {
  if (typeof value !== 'object' || value === null) return false;
  const event = value as Record<string, unknown>;
  if (
    event.schemaVersion !== 2 ||
    event.type !== 'inventory.stock_adjusted' ||
    typeof event.eventId !== 'string' ||
    typeof event.occurredAt !== 'string' ||
    typeof event.payload !== 'object' ||
    event.payload === null
  ) return false;
  const payload = event.payload as Record<string, unknown>;
  return (
    typeof payload.warehouseId === 'string' &&
    typeof payload.sku === 'string' &&
    typeof payload.delta === 'number' &&
    Number.isSafeInteger(payload.delta) &&
    payload.delta !== 0 &&
    typeof payload.reason === 'string' &&
    REASONS.has(payload.reason as AdjustmentReason) &&
    (typeof payload.actorId === 'string' || payload.actorId === null)
  );
}
