// Caches expensive analytics report runs keyed by the request parameters,
// and keeps a small journal that the /debug/cache endpoint renders so we
// can see which queries were hits and which went to the warehouse.

export interface ReportParams {
  metric: string;
  range: { from: string; to: string };
  filters?: Record<string, string | number | boolean>;
  limit?: number;
}

export interface ReportFetcher {
  run(params: ReportParams): Promise<number[]>;
}

export interface JournalEntry {
  params: ReportParams;
  hit: boolean;
}

export class ReportCache {
  private entries: Map<ReportParams, number[]>;
  private journal: JournalEntry[];
  private fetcher: ReportFetcher;

  constructor(fetcher: ReportFetcher) {
    this.fetcher = fetcher;
    this.entries = new Map();
    this.journal = [];
  }

  async run(params: ReportParams): Promise<number[]> {
    const cached = this.entries.get(params);
    if (cached !== undefined) {
      this.journal.push({ params, hit: true });
      return cached;
    }
    const rows = await this.fetcher.run(params);
    this.entries.set(params, rows);
    this.journal.push({ params, hit: false });
    return rows;
  }

  invalidate(params: ReportParams): boolean {
    return this.entries.delete(params);
  }

  stats(): { size: number; journal: JournalEntry[] } {
    return { size: this.entries.size, journal: this.journal };
  }
}
