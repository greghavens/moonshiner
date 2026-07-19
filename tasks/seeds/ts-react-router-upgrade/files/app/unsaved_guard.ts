import type { MemoryDataRouter, NavigationOutcome } from '../router/router_v7.ts';

export interface EditorState {
  readonly dirty: boolean;
}

export class UnsavedGuard {
  private release: (() => void) | null = null;
  private readonly router: MemoryDataRouter;
  private readonly editor: EditorState;

  constructor(router: MemoryDataRouter, editor: EditorState) {
    this.router = router;
    this.editor = editor;
  }

  attach(): () => void {
    this.release = (this.router as any).history.block((next: string) =>
      this.editor.dirty && next !== this.router.location
    );
    return () => this.detach();
  }

  pendingLocation(): string | null {
    return (this.router as any).history.blockedLocation ?? null;
  }

  resolve(choice: 'leave' | 'stay'): NavigationOutcome {
    return choice === 'leave'
      ? (this.router as any).history.proceed()
      : (this.router as any).history.reset();
  }

  private detach(): void {
    this.release?.();
    this.release = null;
  }
}
