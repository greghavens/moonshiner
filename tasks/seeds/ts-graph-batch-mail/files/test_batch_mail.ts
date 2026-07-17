// Acceptance tests for the Microsoft Graph JSON batch mail client.
//
// Spins up a loopback fake of the Graph v1.0 $batch endpoint implementing the
// contract pinned in docs/contract.json: 20-request batches, unordered
// subresponses correlated by id, per-request pagination via opaque nextLinks,
// and per-subrequest 429 throttling. No vendor network, no real credentials.
// The fake serves ONLY POST /v1.0/$batch — any bare GET is a contract breach.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';

import { GraphBatchClient, GraphBatchError } from './batch_mail_client.ts';

const TOKEN = 'dummy-token-4be2d7'; // dummy; must never leak or be logged
const USER = 'u-mia';

const SELECT_META = '$select=id,displayName,totalItemCount';
const SELECT_MSGS = '$select=id,subject,from,receivedDateTime,isRead&$top=5';
const PAGE_SIZE = 2; // the server's own page size; smaller than $top on purpose

interface Msg {
  id: string;
  subject: string;
  fromName: string;
  fromAddress: string;
  receivedDateTime: string;
  isRead: boolean;
}

interface Folder {
  id: string;
  displayName: string;
  messages: Msg[];
  missing?: boolean;
}

interface Route {
  status: number;
  headers?: Record<string, string>;
  body: unknown;
}

interface RecordedPost {
  size: number;
  urls: string[];
  auth: string | undefined;
  contentType: string | undefined;
}

function msg(id: string, subject: string, fromName: string, fromAddress: string,
             receivedDateTime: string, isRead: boolean): Msg {
  return { id, subject, fromName, fromAddress, receivedDateTime, isRead };
}

function metaUrl(fid: string): string {
  return `/users/${USER}/mailFolders/${fid}?${SELECT_META}`;
}

function msgsUrl(fid: string, skiptoken?: string): string {
  return `/users/${USER}/mailFolders/${fid}/messages?${SELECT_MSGS}`
    + (skiptoken ? `&$skiptoken=${skiptoken}` : '');
}

function wireMessage(m: Msg): Record<string, unknown> {
  return {
    id: m.id,
    subject: m.subject,
    from: { emailAddress: { name: m.fromName, address: m.fromAddress } },
    receivedDateTime: m.receivedDateTime,
    isRead: m.isRead,
  };
}

function graphError(code: string, message: string): Record<string, unknown> {
  return { error: { code, message } };
}

class FakeGraphBatch {
  posts: RecordedPost[] = [];
  hits = new Map<string, number>();
  otherRequests = 0;
  badBatchReasons: string[] = [];

  private routes = new Map<string, Route>();
  private throttleQueues = new Map<string, number[]>();
  private alwaysThrottle = new Map<string, number>();
  private server: http.Server;
  base = '';

  constructor(folders: Folder[]) {
    for (const f of folders) this.addFolder(f);
    this.server = http.createServer((req, res) => this.handle(req, res));
  }

  async start(): Promise<void> {
    await new Promise<void>(resolve => this.server.listen(0, '127.0.0.1', resolve));
    const { port } = this.server.address() as AddressInfo;
    this.base = `http://127.0.0.1:${port}/v1.0`;
    // Routes emit absolute nextLinks, so they need the base; rebuild them.
    const folders = this.pendingFolders;
    this.routes.clear();
    for (const f of folders) this.registerFolder(f);
  }

  async stop(): Promise<void> {
    await new Promise<void>(resolve => this.server.close(() => resolve()));
  }

  private pendingFolders: Folder[] = [];

  private addFolder(f: Folder): void {
    this.pendingFolders.push(f);
  }

  private registerFolder(f: Folder): void {
    if (f.missing) {
      const notFound: Route = {
        status: 404,
        body: graphError('ErrorItemNotFound', `The specified folder ${f.id} was not found in the store.`),
      };
      this.routes.set(metaUrl(f.id), notFound);
      this.routes.set(msgsUrl(f.id), notFound);
      return;
    }
    this.routes.set(metaUrl(f.id), {
      status: 200,
      body: { id: f.id, displayName: f.displayName, totalItemCount: f.messages.length },
    });
    const pages: Msg[][] = [];
    for (let i = 0; i < Math.max(1, Math.ceil(f.messages.length / PAGE_SIZE)); i++) {
      pages.push(f.messages.slice(i * PAGE_SIZE, (i + 1) * PAGE_SIZE));
    }
    for (let i = 0; i < pages.length; i++) {
      const skip = i === 0 ? undefined : `${f.id}-pg${i + 1}-c3RhdGU%3D`;
      const url = msgsUrl(f.id, skip);
      const body: Record<string, unknown> = { value: pages[i].map(wireMessage) };
      if (i + 1 < pages.length) {
        body['@odata.nextLink'] = this.base + msgsUrl(f.id, `${f.id}-pg${i + 2}-c3RhdGU%3D`);
      }
      this.routes.set(url, { status: 200, body });
    }
  }

  queueThrottle(url: string, retryAfterSeconds: number[]): void {
    this.throttleQueues.set(url, [...retryAfterSeconds]);
  }

  throttleForever(url: string, retryAfterSeconds: number): void {
    this.alwaysThrottle.set(url, retryAfterSeconds);
  }

  hitCount(url: string): number {
    return this.hits.get(url) ?? 0;
  }

  // Tolerate legal percent-encodings of the OData literals, never of the
  // opaque token bytes: verbatim link reuse is the contract.
  private static normalize(url: string): string {
    return url.replaceAll('%24', '$').replaceAll('%2C', ',').replaceAll('%2c', ',');
  }

  private handle(req: http.IncomingMessage, res: http.ServerResponse): void {
    const chunks: Buffer[] = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      try {
        this.dispatch(req, res, Buffer.concat(chunks).toString('utf8'));
      } catch (err) {
        this.respond(res, 500, graphError('InternalServerError', `mock failure: ${err}`));
      }
    });
  }

  private respond(res: http.ServerResponse, status: number, body: unknown): void {
    const payload = JSON.stringify(body);
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(payload);
  }

  private failBatch(res: http.ServerResponse, reason: string): void {
    this.badBatchReasons.push(reason);
    this.respond(res, 400, graphError('BadRequest', reason));
  }

  private dispatch(req: http.IncomingMessage, res: http.ServerResponse, raw: string): void {
    const url = req.url ?? '';
    if (req.method !== 'POST' || url !== '/v1.0/$batch') {
      this.otherRequests++;
      this.respond(res, 404, graphError('ResourceNotFound',
        `Only POST /v1.0/$batch exists here; got ${req.method} ${url}`));
      return;
    }
    const auth = req.headers.authorization;
    if (auth !== `Bearer ${TOKEN}`) {
      this.respond(res, 401, graphError('InvalidAuthenticationToken', 'Access token is empty or invalid.'));
      return;
    }
    if (!(req.headers['content-type'] ?? '').includes('application/json')) {
      this.failBatch(res, 'batch POST must be Content-Type: application/json');
      return;
    }

    let parsed: { requests?: unknown };
    try {
      parsed = JSON.parse(raw);
    } catch {
      this.failBatch(res, 'batch body is not valid JSON');
      return;
    }
    const requests = parsed.requests;
    if (!Array.isArray(requests) || requests.length === 0) {
      this.failBatch(res, 'batch body must carry a non-empty requests array');
      return;
    }
    if (requests.length > 20) {
      this.failBatch(res, `batch carries ${requests.length} requests; the limit is 20`);
      return;
    }

    const seenIds = new Set<string>();
    const recorded: RecordedPost = {
      size: requests.length,
      urls: [],
      auth,
      contentType: req.headers['content-type'],
    };
    const responses: Array<Record<string, unknown>> = [];

    for (const r of requests as Array<Record<string, unknown>>) {
      if (typeof r.id !== 'string' || r.id.length === 0) {
        this.failBatch(res, 'every subrequest needs a string id');
        return;
      }
      const idKey = r.id.toLowerCase();
      if (seenIds.has(idKey)) {
        this.failBatch(res, `duplicate subrequest id ${r.id}`);
        return;
      }
      seenIds.add(idKey);
      if (r.method !== 'GET') {
        this.failBatch(res, `unexpected subrequest method ${String(r.method)}`);
        return;
      }
      if (typeof r.url !== 'string' || !r.url.startsWith('/')) {
        this.failBatch(res, `subrequest url must be relative and start with '/': ${String(r.url)}`);
        return;
      }
      if ('body' in r && r.body !== undefined && r.body !== null) {
        this.failBatch(res, 'GET subrequests must not carry a body');
        return;
      }

      const norm = FakeGraphBatch.normalize(r.url);
      recorded.urls.push(norm);
      this.hits.set(norm, (this.hits.get(norm) ?? 0) + 1);

      const always = this.alwaysThrottle.get(norm);
      const queue = this.throttleQueues.get(norm);
      let retryAfter: number | undefined;
      if (always !== undefined) retryAfter = always;
      else if (queue !== undefined && queue.length > 0) retryAfter = queue.shift();

      if (retryAfter !== undefined) {
        responses.push({
          id: r.id,
          status: 429,
          headers: { 'Retry-After': String(retryAfter), 'Content-Type': 'application/json' },
          body: graphError('TooManyRequests', 'Please retry again later.'),
        });
        continue;
      }

      const route = this.routes.get(norm);
      if (route === undefined) {
        responses.push({
          id: r.id,
          status: 400,
          headers: { 'Content-Type': 'application/json' },
          body: graphError('BadContinuationToken',
            `Unrecognized resource URL; continuation links must be reused verbatim. Got: ${norm}`),
        });
        continue;
      }
      responses.push({
        id: r.id,
        status: route.status,
        headers: { 'Content-Type': 'application/json', ...(route.headers ?? {}) },
        body: route.body,
      });
    }

    this.posts.push(recorded);
    responses.reverse(); // subresponse order is explicitly not guaranteed
    this.respond(res, 200, { responses });
  }
}

function fixtureFolders(): Folder[] {
  const folders: Folder[] = [
    {
      id: 'f-inbox',
      displayName: 'Inbox',
      messages: [
        msg('m-inbox-1', 'Fire drill Thursday', 'Facilities', 'facilities@contoso.example',
          '2026-05-04T09:12:00Z', false),
        msg('m-inbox-2', 'Budget review notes', 'Emily Braun', 'emily.braun@contoso.example',
          '2026-05-03T15:40:00Z', true),
        msg('m-inbox-3', 'Lunch?', 'Adele Vance', 'adele.vance@contoso.example',
          '2026-05-03T11:05:00Z', false),
        msg('m-inbox-4', 'VPN maintenance window', 'IT Operations', 'it-ops@contoso.example',
          '2026-05-02T22:30:00Z', true),
        msg('m-inbox-5', 'Welcome aboard', 'HR Team', 'hr@contoso.example',
          '2026-05-01T08:00:00Z', true),
      ],
    },
    {
      id: 'f-archive',
      displayName: 'Archive',
      messages: [
        msg('m-arch-1', '2025 goals archive', 'Nestor Wilke', 'nestor.wilke@contoso.example',
          '2025-12-30T10:00:00Z', true),
        msg('m-arch-2', 'Old expense report', 'Finance Bot', 'finance-noreply@contoso.example',
          '2025-11-12T16:20:00Z', true),
      ],
    },
    {
      id: 'f-reports',
      displayName: 'Quarterly Reports',
      messages: [
        msg('m-rep-1', 'Q1 numbers (final)', 'Emily Braun', 'emily.braun@contoso.example',
          '2026-04-02T09:00:00Z', true),
        msg('m-rep-2', 'Q1 numbers (draft 2)', 'Emily Braun', 'emily.braun@contoso.example',
          '2026-03-28T14:30:00Z', true),
        msg('m-rep-3', 'Q1 reporting kickoff', 'Grady Archie', 'grady.archie@contoso.example',
          '2026-03-01T09:15:00Z', false),
      ],
    },
    { id: 'f-missing', displayName: '(gone)', messages: [], missing: true },
    {
      id: 'f-slow',
      displayName: 'Escalations',
      messages: [
        msg('m-slow-1', 'Escalation: printer on fire', 'Service Desk', 'servicedesk@contoso.example',
          '2026-05-04T13:37:00Z', false),
      ],
    },
  ];
  for (let i = 0; i < 13; i++) {
    const n = String(i).padStart(2, '0');
    folders.push({
      id: `f-p${n}`,
      displayName: `Project ${n}`,
      messages: [
        msg(`m-p${n}-1`, `Filler ${n}`, 'Automation', 'automation@contoso.example',
          '2026-04-15T12:00:00Z', true),
      ],
    });
  }
  return folders;
}

const ALL_FOLDER_IDS = fixtureFolders().map(f => f.id);

interface Ctx {
  fake: FakeGraphBatch;
  client: GraphBatchClient;
  delays: number[];
}

async function withFake(opts: { maxRetries?: number; token?: string },
                        fn: (ctx: Ctx) => Promise<void>): Promise<void> {
  const fake = new FakeGraphBatch(fixtureFolders());
  await fake.start();
  const delays: number[] = [];
  const client = new GraphBatchClient({
    baseUrl: fake.base,
    accessToken: opts.token ?? TOKEN,
    delay: async (seconds: number) => { delays.push(seconds); },
    ...(opts.maxRetries === undefined ? {} : { maxRetries: opts.maxRetries }),
  });
  try {
    await fn({ fake, client, delays });
  } finally {
    await fake.stop();
  }
}

test('batches are built to the documented $batch contract and filled to the 20 limit', async () => {
  await withFake({}, async ({ fake, client }) => {
    const results = await client.fetchMail(USER, ALL_FOLDER_IDS);

    assert.equal(results.length, 18);
    // 18 folders x 2 subrequests = 36, split [20, 16]; then two continuation
    // rounds: (inbox pg2 + reports pg2), then (inbox pg3).
    assert.deepEqual(fake.posts.map(p => p.size), [20, 16, 2, 1]);
    assert.equal(fake.badBatchReasons.length, 0);
    assert.equal(fake.otherRequests, 0);
    for (const p of fake.posts) {
      assert.equal(p.auth, `Bearer ${TOKEN}`);
      assert.ok((p.contentType ?? '').includes('application/json'));
      for (const u of p.urls) assert.ok(u.startsWith('/'), `relative url expected, got ${u}`);
    }
    const first = fake.posts[0].urls;
    assert.ok(first.includes(metaUrl('f-inbox')));
    assert.ok(first.includes(msgsUrl('f-inbox')));
    assert.ok(first.includes(metaUrl('f-archive')));
  });
});

test('out-of-order subresponses are correlated by id and decoded', async () => {
  await withFake({}, async ({ client }) => {
    const results = await client.fetchMail(USER, ALL_FOLDER_IDS);

    assert.deepEqual(results.map(r => r.id), ALL_FOLDER_IDS);

    const inbox = results[0];
    assert.equal(inbox.displayName, 'Inbox');
    assert.equal(inbox.totalItemCount, 5);
    assert.equal(inbox.error, null);
    assert.deepEqual(inbox.messages.map(m => m.id),
      ['m-inbox-1', 'm-inbox-2', 'm-inbox-3', 'm-inbox-4', 'm-inbox-5']);
    assert.equal(inbox.messages[0].subject, 'Fire drill Thursday');
    assert.equal(inbox.messages[0].from, 'facilities@contoso.example');
    assert.equal(inbox.messages[0].receivedDateTime, '2026-05-04T09:12:00Z');
    assert.equal(inbox.messages[0].isRead, false);
    assert.equal(inbox.messages[1].isRead, true);

    const archive = results[1];
    assert.equal(archive.displayName, 'Archive');
    assert.equal(archive.totalItemCount, 2);
    assert.deepEqual(archive.messages.map(m => m.subject),
      ['2025 goals archive', 'Old expense report']);
    assert.equal(archive.messages[1].from, 'finance-noreply@contoso.example');

    const filler = results.find(r => r.id === 'f-p07');
    assert.ok(filler);
    assert.equal(filler.displayName, 'Project 07');
    assert.deepEqual(filler.messages.map(m => m.id), ['m-p07-1']);
  });
});

test('per-request pagination follows opaque nextLinks through $batch', async () => {
  await withFake({}, async ({ fake, client }) => {
    const results = await client.fetchMail(USER, ALL_FOLDER_IDS);

    const reports = results[2];
    assert.equal(reports.error, null);
    assert.deepEqual(reports.messages.map(m => m.id), ['m-rep-1', 'm-rep-2', 'm-rep-3']);

    // Each page fetched exactly once, continuations with the token verbatim.
    assert.equal(fake.hitCount(msgsUrl('f-inbox')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-inbox', 'f-inbox-pg2-c3RhdGU%3D')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-inbox', 'f-inbox-pg3-c3RhdGU%3D')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-reports', 'f-reports-pg2-c3RhdGU%3D')), 1);
    assert.equal(fake.otherRequests, 0);
  });
});

test('a throttled subrequest is retried alone after each Retry-After', async () => {
  await withFake({}, async ({ fake, client, delays }) => {
    fake.queueThrottle(msgsUrl('f-slow'), [3, 5]);

    const results = await client.fetchMail(USER, ['f-archive', 'f-slow']);

    assert.deepEqual(delays, [3, 5]);
    assert.deepEqual(fake.posts.map(p => p.size), [4, 1, 1]);
    assert.deepEqual(fake.posts[1].urls, [msgsUrl('f-slow')]);
    assert.deepEqual(fake.posts[2].urls, [msgsUrl('f-slow')]);
    assert.equal(fake.hitCount(metaUrl('f-archive')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-archive')), 1);
    assert.equal(fake.hitCount(metaUrl('f-slow')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-slow')), 3);

    const slow = results[1];
    assert.equal(slow.error, null);
    assert.deepEqual(slow.messages.map(m => m.id), ['m-slow-1']);
  });
});

test('multiple throttled siblings retry together after the longest Retry-After', async () => {
  await withFake({}, async ({ fake, client, delays }) => {
    fake.queueThrottle(msgsUrl('f-archive'), [2]);
    fake.queueThrottle(msgsUrl('f-slow'), [4]);

    const results = await client.fetchMail(USER, ['f-archive', 'f-slow']);

    assert.deepEqual(delays, [4]);
    assert.deepEqual(fake.posts.map(p => p.size), [4, 2]);
    assert.deepEqual([...fake.posts[1].urls].sort(),
      [msgsUrl('f-archive'), msgsUrl('f-slow')].sort());
    assert.deepEqual(results[0].messages.map(m => m.id), ['m-arch-1', 'm-arch-2']);
    assert.deepEqual(results[1].messages.map(m => m.id), ['m-slow-1']);
  });
});

test('throttling exhaustion reports the folder and preserves successful siblings', async () => {
  await withFake({ maxRetries: 2 }, async ({ fake, client, delays }) => {
    fake.throttleForever(msgsUrl('f-slow'), 2);

    const results = await client.fetchMail(USER, ['f-archive', 'f-slow']);

    assert.deepEqual(delays, [2, 2]);
    assert.deepEqual(fake.posts.map(p => p.size), [4, 1, 1]);
    assert.equal(fake.hitCount(msgsUrl('f-slow')), 3);

    const archive = results[0];
    assert.equal(archive.error, null);
    assert.equal(archive.messages.length, 2);

    const slow = results[1];
    assert.ok(slow.error, 'exhausted throttling must surface an error');
    assert.equal(slow.error!.status, 429);
    assert.equal(slow.error!.code, 'TooManyRequests');
    assert.equal(slow.error!.retryAfter, 2);
    // Folder metadata arrived before the throttle bit: keep it.
    assert.equal(slow.displayName, 'Escalations');
    assert.deepEqual(slow.messages, []);

    assert.ok(!JSON.stringify(results).includes(TOKEN), 'access token must never leak');
  });
});

test('a missing folder is a terminal per-folder error, never retried', async () => {
  await withFake({}, async ({ fake, client, delays }) => {
    const results = await client.fetchMail(USER, ['f-missing', 'f-archive']);

    assert.deepEqual(delays, []);
    assert.equal(fake.posts.length, 1);
    assert.equal(fake.hitCount(metaUrl('f-missing')), 1);
    assert.equal(fake.hitCount(msgsUrl('f-missing')), 1);

    const missing = results[0];
    assert.ok(missing.error);
    assert.equal(missing.error!.status, 404);
    assert.equal(missing.error!.code, 'ErrorItemNotFound');
    assert.ok(missing.error!.message.includes('f-missing'));
    assert.deepEqual(missing.messages, []);

    const archive = results[1];
    assert.equal(archive.error, null);
    assert.equal(archive.messages.length, 2);
  });
});

test('a batch-level failure raises GraphBatchError', async () => {
  await withFake({ token: 'wrong-token' }, async ({ client }) => {
    await assert.rejects(
      () => client.fetchMail(USER, ['f-archive']),
      (err: unknown) => {
        assert.ok(err instanceof GraphBatchError);
        assert.equal(err.status, 401);
        assert.equal(err.code, 'InvalidAuthenticationToken');
        return true;
      });
  });
});
