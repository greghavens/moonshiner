export type Row = Record<string, unknown>;

export type ColumnSpec = {
  header: string;
  key: string;
  format?: (value: unknown) => string;
};

function csvEscape(cell: string): string {
  if (/[",\n]/.test(cell)) {
    return '"' + cell.replaceAll('"', '""') + '"';
  }
  return cell;
}

/**
 * Compile the column specs into one accessor per column. Reports render the
 * same spec against thousands of rows, so the specs are resolved up front.
 */
export function buildAccessors(columns: ColumnSpec[]): Array<(row: Row) => string> {
  const accessors: Array<(row: Row) => string> = [];
  for (var i = 0; i < columns.length; i++) {
    var spec = columns[i];
    accessors.push((row: Row) => {
      const raw = row[spec.key];
      const text = spec.format ? spec.format(raw) : String(raw ?? '');
      return csvEscape(text);
    });
  }
  return accessors;
}

export function renderReport(columns: ColumnSpec[], rows: Row[]): string {
  const accessors = buildAccessors(columns);
  const header = columns.map((c) => csvEscape(c.header)).join(',');
  const body = rows.map((row) => accessors.map((cell) => cell(row)).join(','));
  return [header, ...body].join('\n');
}
