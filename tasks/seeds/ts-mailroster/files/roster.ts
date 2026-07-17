// roster.ts — thin client for the newsletter roster service.
//
// The transport is injected (the CLI passes a fetch wrapper, tests pass a
// fake), so this module owns request shaping and error mapping only.
// Nothing here ever rejects: every method resolves to a result object,
// { ok: true, data } or { ok: false, code, message, ... }, and callers
// are expected to check `ok` before touching `data`.

export type TransportRequest = {
  method: string;
  path: string;
  body?: unknown;
};

export type TransportResponse = {
  status: number;
  body: any;
};

export type Transport = (req: TransportRequest) => Promise<TransportResponse>;

export type RosterResult =
  | { ok: true; data: any }
  | {
      ok: false;
      code: string;
      message: string;
      fields?: string[];
      retryAfterSeconds?: number;
    };

function unexpected(status: number): RosterResult {
  return { ok: false, code: 'api', message: `unexpected status ${status}` };
}

function networkFailure(err: any): RosterResult {
  return { ok: false, code: 'network', message: `request failed: ${err.message}` };
}

export function createRosterClient(transport: Transport) {
  return {
    getSubscriber(id: string): Promise<RosterResult> {
      return transport({ method: 'GET', path: `/subscribers/${id}` })
        .then((res) => {
          if (res.status === 200) {
            return { ok: true, data: res.body } as RosterResult;
          }
          if (res.status === 404) {
            return {
              ok: false,
              code: 'not_found',
              message: `subscriber ${id} not found`,
            } as RosterResult;
          }
          return unexpected(res.status);
        })
        .catch((err) => networkFailure(err));
    },

    subscribe(email: string, name: string): Promise<RosterResult> {
      return transport({
        method: 'POST',
        path: '/subscribers',
        body: { email, name },
      })
        .then((res) => {
          if (res.status === 201) {
            return { ok: true, data: res.body } as RosterResult;
          }
          if (res.status === 409) {
            return {
              ok: false,
              code: 'already_subscribed',
              message: `${email} is already subscribed`,
            } as RosterResult;
          }
          if (res.status === 422) {
            const fields = (res.body && res.body.fields) || [];
            return {
              ok: false,
              code: 'invalid',
              message: `invalid subscriber: ${fields.join(', ')}`,
              fields,
            } as RosterResult;
          }
          if (res.status === 429) {
            const wait = (res.body && res.body.retryAfterSeconds) || 0;
            return {
              ok: false,
              code: 'rate_limited',
              message: `rate limited, retry in ${wait}s`,
              retryAfterSeconds: wait,
            } as RosterResult;
          }
          return unexpected(res.status);
        })
        .catch((err) => networkFailure(err));
    },

    unsubscribe(id: string): Promise<RosterResult> {
      return transport({ method: 'DELETE', path: `/subscribers/${id}` })
        .then((res) => {
          if (res.status === 204) {
            return { ok: true, data: null } as RosterResult;
          }
          if (res.status === 404) {
            return {
              ok: false,
              code: 'not_found',
              message: `subscriber ${id} not found`,
            } as RosterResult;
          }
          return unexpected(res.status);
        })
        .catch((err) => networkFailure(err));
    },
  };
}
