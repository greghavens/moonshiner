import type {
  AdjustmentReason,
  StockAdjustedV2,
} from '../../packages/contracts/stock_event.ts';

export interface StockAdjustmentInput {
  eventId: string;
  occurredAt: string;
  warehouseId: string;
  sku: string;
  delta: number;
  reason: AdjustmentReason;
  actorId: string | null;
}

export function produceStockAdjusted(input: StockAdjustmentInput): StockAdjustedV2 {
  return {
    schemaVersion: 2,
    type: 'inventory.stock_adjusted',
    eventId: input.eventId,
    occurredAt: input.occurredAt,
    payload: {
      warehouseId: input.warehouseId,
      sku: input.sku,
      delta: input.delta,
      reason: input.reason,
      actorId: input.actorId,
    },
  };
}
