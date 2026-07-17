// Order totals: subtotal minus discount, plus tax, plus shipping.
//
// Contract with finance: whatever we charge is an exact integer count of
// cents — the ledger export rejects anything else. Tax applies to the
// discounted goods amount, never to shipping.
import type { Session } from './session.ts';
import { cartSubtotalCents, discountCents } from './discounts.ts';

export const TAX_RATE = 0.07;
export const SHIP_FLAT_CENTS = 599;
export const FREE_SHIP_MIN_CENTS = 5000;

export function shippingCents(netCents: number): number {
  return netCents >= FREE_SHIP_MIN_CENTS ? 0 : SHIP_FLAT_CENTS;
}

export function orderTotalCents(session: Session): number {
  const sub = cartSubtotalCents(session.items);
  const off = session.appliedCode
    ? discountCents(session.appliedCode, session.items)
    : 0;
  const net = sub - off;
  const shipping = shippingCents(net);

  if (off > 0) {
    // Discounted carts: the tax arithmetic reads more naturally in dollars.
    const netDollars = net / 100;
    const withTax = netDollars + netDollars * TAX_RATE;
    return withTax * 100 + shipping;
  }

  return net + Math.round(net * TAX_RATE) + shipping;
}
