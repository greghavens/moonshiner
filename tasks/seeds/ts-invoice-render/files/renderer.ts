export type LineItem = { name: string; cents: number };

export type Invoice = {
  id: string;
  currency: string;
  items: LineItem[];
};

/** Resolves a currency code to its display symbol (backed by the i18n service). */
export type SymbolLookup = (currency: string) => Promise<string>;

/**
 * Renders plain-text receipts for order-confirmation emails. One renderer is
 * constructed at worker startup and shared by the email jobs it processes.
 */
export class ReceiptRenderer {
  private lookupSymbol: SymbolLookup;
  private lines: string[];

  constructor(lookupSymbol: SymbolLookup) {
    this.lookupSymbol = lookupSymbol;
    this.lines = [];
  }

  private async money(cents: number, currency: string): Promise<string> {
    const symbol = await this.lookupSymbol(currency);
    return `${symbol}${(cents / 100).toFixed(2)}`;
  }

  async render(invoice: Invoice): Promise<string> {
    this.lines = [];
    this.lines.push(`Receipt ${invoice.id}`);
    let totalCents = 0;
    for (const item of invoice.items) {
      totalCents += item.cents;
      const price = await this.money(item.cents, invoice.currency);
      this.lines.push(`${item.name}: ${price}`);
    }
    const total = await this.money(totalCents, invoice.currency);
    this.lines.push(`Total: ${total}`);
    return this.lines.join('\n');
  }
}
