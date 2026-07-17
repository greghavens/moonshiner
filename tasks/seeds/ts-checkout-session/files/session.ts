// Checkout session state: the cart, one applied promo code, payment
// capture and the refund ledger. Every amount in the system is an integer
// count of minor units (cents).
import { promoExists } from './discounts.ts';

export interface Item {
  sku: string;
  name: string;
  unitCents: number;
  qty: number;
}

export interface Refund {
  amountCents: number;
  receiptId: string | null;
}

export interface Session {
  id: string;
  items: Item[];
  appliedCode: string | null;
  paidCents: number;
  refunds: Refund[];
}

export interface CaptureReceipt {
  id: string;
}

// The payment-gateway client is injected so checkout stays testable.
export type Capture = (amountCents: number) => Promise<CaptureReceipt>;

export function createSession(id: string, items: Item[]): Session {
  for (const item of items) {
    if (!Number.isInteger(item.unitCents) || item.unitCents < 0) {
      throw new Error(`bad unit price for ${item.sku}`);
    }
    if (!Number.isInteger(item.qty) || item.qty < 1) {
      throw new Error(`bad quantity for ${item.sku}`);
    }
  }
  return {
    id,
    items: items.map((item) => ({ ...item })),
    appliedCode: null,
    paidCents: 0,
    refunds: [],
  };
}

export function setQuantity(session: Session, sku: string, qty: number): void {
  if (!Number.isInteger(qty) || qty < 1) throw new Error(`bad quantity for ${sku}`);
  const line = session.items.find((item) => item.sku === sku);
  if (!line) throw new Error(`no cart line for ${sku}`);
  line.qty = qty;
}

export function applyCode(session: Session, code: string): void {
  if (!promoExists(code)) throw new Error(`unknown promo code: ${code}`);
  session.appliedCode = code;
}

export function markPaid(session: Session, amountCents: number): void {
  if (!Number.isInteger(amountCents) || amountCents <= 0) {
    throw new Error('paid amount must be a positive integer of cents');
  }
  session.paidCents = amountCents;
}

export function refundedTotal(session: Session): number {
  let sum = 0;
  for (const refund of session.refunds) sum += refund.amountCents;
  return sum;
}

export function refundLedger(session: Session): number[] {
  return session.refunds.map((refund) => refund.amountCents);
}

// Issue a refund against this order: validate it still fits under the
// amount paid, capture it with the gateway, then record it in the ledger.
export async function requestRefund(
  session: Session,
  amountCents: number,
  capture: Capture,
): Promise<string> {
  if (!Number.isInteger(amountCents) || amountCents <= 0) {
    throw new Error('refund amount must be a positive integer of cents');
  }
  if (refundedTotal(session) + amountCents > session.paidCents) {
    throw new Error('refund exceeds amount paid');
  }
  const receipt = await capture(amountCents);
  session.refunds.push({ amountCents, receiptId: receipt.id });
  return receipt.id;
}
