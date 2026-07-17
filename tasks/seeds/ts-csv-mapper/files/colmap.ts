// Column mapper for imported CSV data. The CSV is already parsed into a
// rows array (first row = headers); this module matches headers to column
// specs and produces one object per data row. Header matching trims
// whitespace and ignores case, because exports from partner systems are
// wildly inconsistent about both.

export interface ColumnSpec {
  /** Header text to look for in the file. */
  header: string;
  /** Property name in the produced objects. */
  key: string;
  required?: boolean;
}

function normalizeHeader(header: string): string {
  return header.trim().toLowerCase();
}

/** Maps each column spec to its index in the header row (or -1). */
export function resolveColumns(headers: string[], specs: ColumnSpec[]): number[] {
  const normalized = headers.map(normalizeHeader);
  return specs.map((spec) => {
    const index = normalized.indexOf(normalizeHeader(spec.header));
    if (index === -1 && spec.required) {
      throw new Error(`required column "${spec.header}" not found`);
    }
    return index;
  });
}

export function mapRows(
  rows: string[][],
  specs: ColumnSpec[],
): Record<string, string>[] {
  if (rows.length === 0) throw new Error('missing header row');
  const indices = resolveColumns(rows[0], specs);

  const result: Record<string, string>[] = [];
  for (const row of rows.slice(1)) {
    const record: Record<string, string> = {};
    for (let s = 0; s < specs.length; s++) {
      const index = indices[s];
      if (index === -1) continue;
      record[specs[s].key] = row[index] ?? '';
    }
    result.push(record);
  }
  return result;
}
