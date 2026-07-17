// Loads one attachment preview for the message pane. The pane owns an
// AbortController per preview: switching messages aborts the old preview's
// load. Each PreviewLoader instance runs exactly one load; the pane renders
// straight from snapshot(), and the transitions list feeds the debug overlay,
// so state changes here are user-visible immediately.

export type LoaderState = 'idle' | 'loading' | 'loaded' | 'aborted' | 'failed';

export interface Snapshot {
  state: LoaderState;
  data: string | null;
  error: string | null;
}

export type Fetcher = (signal: AbortSignal) => Promise<string>;

export class PreviewLoader {
  readonly transitions: LoaderState[] = [];
  private current: LoaderState = 'idle';
  private data: string | null = null;
  private error: string | null = null;

  get state(): LoaderState {
    return this.current;
  }

  snapshot(): Snapshot {
    return { state: this.current, data: this.data, error: this.error };
  }

  async load(fetcher: Fetcher, signal: AbortSignal): Promise<Snapshot> {
    this.setState('loading');
    signal.addEventListener('abort', () => {
      this.data = null;
      this.error = 'aborted';
      this.setState('aborted');
    });
    try {
      const body = await fetcher(signal);
      this.data = body;
      this.setState('loaded');
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.setState('failed');
    }
    return this.snapshot();
  }

  private setState(next: LoaderState): void {
    this.current = next;
    this.transitions.push(next);
  }
}
