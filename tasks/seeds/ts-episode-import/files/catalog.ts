export type Episode = {
  guid: string;
  title: string;
  durationS: number;
};

function tick(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

/**
 * Episode catalog backing the podcast app. In production this sits on
 * sqlite, so every operation is async and settles on a later tick — callers
 * must await their writes like they would with the real database.
 */
export class Catalog {
  private byGuid = new Map<string, Episode>();

  async has(guid: string): Promise<boolean> {
    await tick();
    return this.byGuid.has(guid);
  }

  async put(episode: Episode): Promise<void> {
    await tick();
    this.byGuid.set(episode.guid, episode);
  }

  async get(guid: string): Promise<Episode | undefined> {
    await tick();
    return this.byGuid.get(guid);
  }

  async count(): Promise<number> {
    await tick();
    return this.byGuid.size;
  }

  async allGuids(): Promise<string[]> {
    await tick();
    return [...this.byGuid.keys()].sort();
  }
}
