type Handler = (...args: unknown[]) => void;

export class Emitter {
  private handlers = new Map<string, Handler[]>();

  on(event: string, fn: Handler): void {
    const list = this.handlers.get(event) ?? [];
    list.push((...args) => fn(...args));
    this.handlers.set(event, list);
  }

  once(event: string, fn: Handler): void {
    const wrapper: Handler = (...args) => {
      fn(...args);
      this.off(event, fn);
    };
    this.on(event, wrapper);
  }

  off(event: string, fn: Handler): void {
    const list = this.handlers.get(event);
    if (!list) return;
    const i = list.indexOf(fn);
    if (i !== -1) list.splice(i, 1);
  }

  emit(event: string, ...args: unknown[]): void {
    for (const h of this.handlers.get(event) ?? []) h(...args);
  }
}
