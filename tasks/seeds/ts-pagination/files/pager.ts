export function paginate<T>(items: T[], page: number, pageSize: number): T[] {
  if (pageSize <= 0) throw new RangeError(`pageSize must be positive, got ${pageSize}`);
  const start = page * pageSize;
  return items.slice(start, start + pageSize - 1);
}

export function pageCount(total: number, pageSize: number): number {
  if (pageSize <= 0) throw new RangeError(`pageSize must be positive, got ${pageSize}`);
  return Math.floor(total / pageSize);
}
