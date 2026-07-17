// Minimal middleware pipeline for the internal HTTP gateway. Middleware
// run in registration order; each decides whether to pass control on by
// calling next(). Code after next() runs on the way back out, innermost
// first. A middleware that skips next() short-circuits the rest.

export interface Context {
  path: string;
  state: Record<string, unknown>;
  response?: unknown;
}

export type Middleware = (ctx: Context, next: () => void) => void;

export class Pipeline {
  private stack: Middleware[] = [];

  use(middleware: Middleware): this {
    this.stack.push(middleware);
    return this;
  }

  run(ctx: Context): Context {
    const dispatch = (index: number): void => {
      if (index >= this.stack.length) return;
      let called = false;
      const next = (): void => {
        if (called) throw new Error('next() called twice');
        called = true;
        dispatch(index + 1);
      };
      this.stack[index](ctx, next);
    };
    dispatch(0);
    return ctx;
  }
}
