// Shelf-label lines for the store's label printer. The printer merges the
// price itself at print time: it substitutes its ${price} token with the
// figure from the pricing service seconds before the label is cut, so a
// rendered template must carry that token through to the device.
//
// Rows arrive from the supplier feed as flat string records.

export type FeedRow = Record<string, string>;

const SIZE = new RegExp("(\d+(\.\d+)?) ?(kg|g|ml|cl|l)\b", "i");

export interface UnitSize {
  qty: number;
  unit: string;
}

export function labelTemplate(row: FeedRow): string {
  const { name, price, unit } = row;
  return `${name} — ${price} / ${unit}`;
}

export function unitSize(name: string): UnitSize | null {
  const m = SIZE.exec(name);
  if (!m) return null;
  return { qty: Number(m[1]), unit: m[3].toLowerCase() };
}

export function labelLines(rows: FeedRow[]): string[] {
  return rows.map((row) => {
    const size = unitSize(row.name);
    const tpl = labelTemplate(row);
    return size ? `${tpl} (${size.qty} ${size.unit})` : tpl;
  });
}
