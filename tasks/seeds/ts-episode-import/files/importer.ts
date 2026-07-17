import { Catalog } from './catalog.ts';
import type { Episode } from './catalog.ts';

export type FeedItem = {
  guid?: string;
  title?: string;
  durationS?: number;
};

export type ImportReport = {
  imported: number;
  skipped: number;
  failures: { guid: string; reason: string }[];
};

/**
 * Sync a parsed RSS feed into the catalog. Episodes already present are
 * left untouched and counted as skipped; malformed items are recorded in
 * the report without aborting the rest of the feed. The returned report
 * is what the cron job logs and alerts on, so it has to reflect what
 * actually happened.
 */
export async function importFeed(catalog: Catalog, items: FeedItem[]): Promise<ImportReport> {
  const report: ImportReport = { imported: 0, skipped: 0, failures: [] };
  items.forEach(async (item) => {
    try {
      if (item.guid && (await catalog.has(item.guid))) {
        report.skipped++;
        return;
      }
      const episode = normalize(item);
      await catalog.put(episode);
      report.imported++;
    } catch (err) {
      report.failures.push({
        guid: item.guid ?? '<missing>',
        reason: (err as Error).message,
      });
    }
  });
  return report;
}

function normalize(item: FeedItem): Episode {
  if (!item.guid) {
    throw new Error('missing guid');
  }
  if (!item.title || item.title.trim() === '') {
    throw new Error('missing title');
  }
  const durationS = item.durationS ?? 0;
  if (durationS < 0) {
    throw new Error('negative duration');
  }
  return { guid: item.guid, title: item.title.trim(), durationS };
}
