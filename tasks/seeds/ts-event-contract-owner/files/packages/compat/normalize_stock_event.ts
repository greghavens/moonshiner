import {
  isStockAdjustedV2,
  type AdjustmentReason,
  type StockAdjustedV2,
} from '../contracts/stock_event.ts';

interface LegacyStockAdjustedV1 {
  version: 1;
  event: 'stock.adjusted';
  id: string;
  timestamp: string;
  warehouse: string;
  sku: string;
  change: string;
  cause: string;
  operator: string | null;
}

function isLegacyEnvelope(value: unknown): value is LegacyStockAdjustedV1 {
  if (typeof value !== 'object' || value === null) return false;
  const event = value as Record<string, unknown>;
  return (
    event.version === 1 &&
    event.event === 'stock.adjusted' &&
    typeof event.id === 'string' && event.id.length > 0 &&
    typeof event.timestamp === 'string' &&
    typeof event.warehouse === 'string' && event.warehouse.length > 0 &&
    typeof event.sku === 'string' && event.sku.length > 0 &&
    typeof event.change === 'string' &&
    typeof event.cause === 'string' &&
    (typeof event.operator === 'string' || event.operator === null)
  );
}

function copyCurrent(event: StockAdjustedV2): StockAdjustedV2 {
  return { ...event, payload: { ...event.payload } };
}

export function normalizeStockAdjusted(input: unknown): StockAdjustedV2 {
  if (isStockAdjustedV2(input)) return copyCurrent(input);
  if (!isLegacyEnvelope(input)) throw new TypeError('invalid stock-adjusted event');
  return {
    schemaVersion: 2,
    type: 'inventory.stock_adjusted',
    eventId: input.id,
    occurredAt: input.timestamp,
    payload: {
      warehouseId: input.warehouse,
      sku: input.sku,
      delta: input.change as unknown as number,
      reason: input.cause as AdjustmentReason,
      actorId: input.operator,
    },
  };
}
