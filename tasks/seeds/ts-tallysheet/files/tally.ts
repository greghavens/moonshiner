// Fixed-width tally sheets for store audit counts.
//
// Auditors walk the aisles with a scanner; the exporter prints one sheet
// per store as plain monospaced text that goes straight to the label
// printers. Layout (all lines end in "\n"):
//
//   section line:  "## " + title, truncated then space-padded to 44 chars
//   row line:      sku   (truncated/padded to 14)
//                  desc  (truncated/padded to 24)
//                  qty   (String(qty) right-aligned to 6; a numeral wider
//                         than 6 is kept whole, never truncated)
//
// Copy accounting: the perf suite injects onCopy through SheetOpts and
// budgets it. The builder must report, via onCopy(result.length), every
// string it produces while assembling sheet text by padding, by
// concatenation (+, +=, template literals), by joining, or by any
// equivalent mechanism that materializes sheet text. Plain slices and
// number-to-string conversions don't count. Reporting is cumulative
// across the whole builder lifetime, render() included.

const WIDTH = 44;
const SKU_W = 14;
const DESC_W = 24;
const QTY_W = 6;

export type SheetOpts = { onCopy?: (chars: number) => void };

export class SheetBuilder {
  _out: string;
  _copy: (chars: number) => void;

  constructor(opts: SheetOpts = {}) {
    this._out = "";
    const hook = opts.onCopy;
    this._copy = hook ? hook : () => {};
  }

  // Account for a string we just materialized, then hand it back.
  _mk(s: string): string {
    this._copy(s.length);
    return s;
  }

  _line(body: string): void {
    const line = this._mk(body + "\n");
    this._out = this._mk(this._out + line);
  }

  section(title: string): void {
    const head = this._mk("## " + title);
    this._line(this._mk(head.slice(0, WIDTH).padEnd(WIDTH)));
  }

  row(sku: string, desc: string, qty: number): void {
    const c1 = this._mk(sku.slice(0, SKU_W).padEnd(SKU_W));
    const c2 = this._mk(desc.slice(0, DESC_W).padEnd(DESC_W));
    const c3 = this._mk(String(qty).padStart(QTY_W));
    this._line(this._mk(c1 + c2 + c3));
  }

  render(): string {
    return this._out;
  }
}
