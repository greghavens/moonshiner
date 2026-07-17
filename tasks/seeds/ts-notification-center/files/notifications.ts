// In-app notification center. Notifications get a sequential id and a
// timestamp from an injectable clock (tests pass a fake); list() returns
// newest first. Nothing here talks to the network — transports subscribe
// elsewhere.

export interface NotificationInput {
  kind: string;
  title: string;
  body?: string;
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body?: string;
  createdAt: number;
}

export interface CenterOptions {
  now?: () => number;
}

export class NotificationCenter {
  private items: Notification[] = [];
  private seq = 0;
  private now: () => number;

  constructor(options: CenterOptions = {}) {
    this.now = options.now ?? Date.now;
  }

  publish(input: NotificationInput): Notification {
    if (!input.kind || !input.title) {
      throw new Error('notifications need a kind and a title');
    }
    const notification: Notification = {
      id: `n${++this.seq}`,
      kind: input.kind,
      title: input.title,
      createdAt: this.now(),
    };
    if (input.body !== undefined) notification.body = input.body;
    this.items.push(notification);
    return notification;
  }

  /** Newest first; ties broken by publish order, latest first. */
  list(): Notification[] {
    return [...this.items].sort(
      (a, b) => b.createdAt - a.createdAt || b.id.localeCompare(a.id, undefined, { numeric: true }),
    );
  }

  dismiss(id: string): boolean {
    const index = this.items.findIndex((n) => n.id === id);
    if (index === -1) return false;
    this.items.splice(index, 1);
    return true;
  }

  count(): number {
    return this.items.length;
  }
}
