// Projects raw helpdesk webhook payloads into the flat rows the agent
// dashboard renders. The payload shape mirrors the webhook guide's
// documented ticket object (see RawTicket).

export interface RawTicket {
  id: string;
  requester: { name: string; contact: { email: string; phone?: string } };
  org?: { name: string; plan?: string };
  thread: { messages: { author: string; body: string }[] };
  metrics: { sla: { breachAt: string | null } };
}

export interface TicketRow {
  id: string;
  requesterEmail: string;
  orgName: string;
  lastReply: string;
  breachAt: string | null;
}

export class ProjectionError extends Error {
  readonly path: string;
  constructor(path: string) {
    super(`ticket field missing: ${path}`);
    this.name = 'ProjectionError';
    this.path = path;
  }
}

function required<T>(value: T | null | undefined, path: string): T {
  if (value === undefined || value === null) throw new ProjectionError(path);
  return value;
}

export function buildTicketRow(raw: unknown): TicketRow {
  if (typeof raw !== 'object' || raw === null) throw new ProjectionError('$');
  const t = raw as RawTicket;
  const messages = t.thread.messages;
  const last = messages[messages.length - 1];
  return {
    id: required(t.id, 'id'),
    requesterEmail: required(t.requester.contact.email, 'requester.contact.email').toLowerCase(),
    orgName: t.org !== undefined ? t.org.name : '(none)',
    lastReply: last.body.slice(0, 80),
    breachAt: t.metrics.sla.breachAt ?? null,
  };
}

export function buildTicketRows(list: unknown[]): {
  rows: TicketRow[];
  problems: { index: number; path: string }[];
} {
  const rows: TicketRow[] = [];
  const problems: { index: number; path: string }[] = [];
  list.forEach((raw, index) => {
    try {
      rows.push(buildTicketRow(raw));
    } catch (err) {
      if (err instanceof ProjectionError) {
        problems.push({ index, path: err.path });
      } else {
        throw err;
      }
    }
  });
  return { rows, problems };
}
