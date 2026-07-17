// Acceptance tests for the Snowflake asynchronous statement controller.
//
// Spins up a loopback fake SQL API v2 endpoint implementing the subset pinned
// in docs/contract.json. No vendor network, no real credentials, no real
// timers: the controller must route every wait through the injected wait()
// and read time only from the injected now(). Protected — do not modify.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';

import {
  StatementController,
  StatementFailedError,
  SqlApiError,
} from './statement_controller.ts';

const CONTRACT = JSON.parse(
  readFileSync(new URL('./docs/contract.json', import.meta.url), 'utf8'));
const SOURCES = JSON.parse(
  readFileSync(new URL('./docs/official_sources.json', import.meta.url), 'utf8'));

const TOKEN = 'dummy-pat-4fd02b881c'; // dummy; must never leak into errors
const TOKEN_TYPE = CONTRACT.auth.token_type as string;
const USER_AGENT = CONTRACT.auth.request_headers['User-Agent'] as string;
const BASE_PATH = CONTRACT.base_path as string;
const POLL_MS = CONTRACT.polling.interval_ms as number;

const H = '01b70000-0000-4000-8000-00000cafe001';
const STATUS_URL = `${BASE_PATH}/${H}?requestId=22222222-2222-4222-8222-222222222222`;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function queryStatus(): unknown {
  return {
    code: '333334',
    message: 'Asynchronous execution in progress. Use provided query id to perform query monitoring and management.',
    statementHandle: H,
    statementStatusUrl: STATUS_URL,
  };
}

function resultBody(): unknown {
  return {
    code: '090001',
    sqlState: '00000',
    message: 'Statement executed successfully.',
    statementHandle: H,
    createdOn: 1752724800000,
    statementStatusUrl: `${BASE_PATH}/${H}`,
    resultSetMetaData: {
      numRows: 2,
      format: 'jsonv2',
      rowType: [
        { name: 'BATCH_ID', type: 'FIXED', length: 0, precision: 38, scale: 0, nullable: false },
        { name: 'STATUS', type: 'TEXT', length: 16777216, precision: 0, scale: 0, nullable: true },
      ],
      partitionInfo: [{ rowCount: 2, uncompressedSize: 64 }],
    },
    data: [['311', 'loaded'], ['312', null]],
  };
}

function cancelStatus(): unknown {
  return {
    code: '000604',
    sqlState: '57014',
    message: 'SQL execution canceled.',
    statementHandle: H,
    statementStatusUrl: `${BASE_PATH}/${H}`,
  };
}

function canceledFailure(): unknown {
  return {
    code: '000604',
    sqlState: '57014',
    message: 'SQL execution canceled.',
    statementHandle: H,
  };
}

function compileFailure(): unknown {
  return {
    code: '001003',
    sqlState: '42000',
    message: "SQL compilation error:\nsyntax error line 1 at position 7 unexpected 'FORM'.",
    statementHandle: H,
  };
}

interface Recorded {
  method: string;
  path: string;
  raw: string;
  params: Record<string, string>;
  headers: http.IncomingHttpHeaders;
  body: string;
}

interface Planned {
  status: number;
  body: unknown;
}

class FakeSnowflake {
  requests: Recorded[] = [];
  submitPlan: Planned[] = [];
  pollPlan: Planned[] = [];
  cancelPlan: Planned[] = [];
  baseUrl = '';
  private server: http.Server;

  constructor() {
    this.server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        const url = new URL(req.url ?? '/', 'http://loopback');
        const params: Record<string, string> = {};
        for (const [k, v] of url.searchParams) params[k] = v;
        this.requests.push({
          method: req.method ?? '',
          path: url.pathname,
          raw: req.url ?? '',
          params,
          headers: req.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        });
        const reply = (planned: Planned | undefined, fallback: Planned) => {
          const p = planned ?? fallback;
          const text = JSON.stringify(p.body);
          res.writeHead(p.status, { 'content-type': 'application/json' });
          res.end(text);
        };
        if (req.method === 'POST' && url.pathname === BASE_PATH) {
          reply(this.submitPlan.shift(), { status: 202, body: queryStatus() });
        } else if (req.method === 'POST' && url.pathname === `${BASE_PATH}/${H}/cancel`) {
          reply(this.cancelPlan.shift(), { status: 200, body: cancelStatus() });
        } else if (req.method === 'GET' && url.pathname === `${BASE_PATH}/${H}`) {
          reply(this.pollPlan.shift(), { status: 202, body: queryStatus() });
        } else {
          reply(undefined, { status: 404, body: { message: 'unknown endpoint' } });
        }
      });
    });
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve));
    this.baseUrl = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
  }

  async close(): Promise<void> {
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }

  posts(path: string): Recorded[] {
    return this.requests.filter((r) => r.method === 'POST' && r.path === path);
  }

  polls(): Recorded[] {
    return this.requests.filter((r) => r.method === 'GET');
  }

  cancels(): Recorded[] {
    return this.posts(`${BASE_PATH}/${H}/cancel`);
  }
}

function uuidSeq(): () => string {
  let n = 0;
  return () => {
    n += 1;
    return `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  };
}

interface Harness {
  fake: FakeSnowflake;
  controller: StatementController;
  clock: { t: number };
  waits: Array<{ ms: number; signalOk: boolean }>;
}

async function harness(opts?: { onWait?: (call: number) => void }): Promise<Harness> {
  const fake = new FakeSnowflake();
  await fake.start();
  const clock = { t: 0 };
  const waits: Array<{ ms: number; signalOk: boolean }> = [];
  const controller = new StatementController({
    baseUrl: fake.baseUrl,
    token: TOKEN,
    tokenType: TOKEN_TYPE,
    userAgent: USER_AGENT,
    requestIds: uuidSeq(),
    now: () => clock.t,
    wait: async (ms: number, signal?: AbortSignal) => {
      waits.push({ ms, signalOk: signal === undefined || signal instanceof AbortSignal });
      clock.t += ms;
      opts?.onWait?.(waits.length);
    },
  });
  return { fake, controller, clock, waits };
}

function checkCommonHeaders(r: Recorded, wantContentType: boolean): void {
  assert.equal(r.headers['authorization'], `Bearer ${TOKEN}`,
    'every request must carry Authorization: Bearer <token>');
  assert.equal(r.headers['x-snowflake-authorization-token-type'], TOKEN_TYPE);
  assert.equal(r.headers['accept'], 'application/json');
  assert.equal(r.headers['user-agent'], USER_AGENT,
    'the SQL API requires a real User-Agent; the runtime default is not ours');
  if (wantContentType) {
    assert.match(String(r.headers['content-type']), /^application\/json/);
  }
}

test('submit sends the documented async shape', async () => {
  const { fake, controller } = await harness();
  try {
    fake.pollPlan.push({ status: 200, body: resultBody() });
    await controller.execute(
      { statement: 'call ops.rebuild_partition_stats()', timeout: 300, warehouse: 'WH_OPS', role: 'OPS' },
      {},
    );
    const submits = fake.posts(BASE_PATH);
    assert.equal(submits.length, 1, 'one statement means exactly one submission');
    const s = submits[0];
    checkCommonHeaders(s, true);
    assert.equal(s.params['async'], 'true',
      'this controller always submits with async=true');
    assert.equal(s.params['requestId'], '00000000-0000-4000-8000-000000000001',
      'requestId must come from the injected factory');
    assert.deepEqual(JSON.parse(s.body), {
      statement: 'call ops.rebuild_partition_stats()',
      timeout: 300,
      warehouse: 'WH_OPS',
      role: 'OPS',
    }, 'unset context fields must be absent from the body, not null');
  } finally {
    await fake.close();
  }
});

test('async submission polls exactly the returned status URL until success', async () => {
  const { fake, controller, waits } = await harness();
  try {
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 200, body: resultBody() });
    const outcome = await controller.execute({ statement: 'select * from etl.batches' }, {});

    assert.equal(outcome.kind, 'succeeded');
    if (outcome.kind === 'succeeded') {
      assert.equal(outcome.statementHandle, H);
      assert.equal(outcome.code, '090001');
      assert.equal(outcome.numRows, 2);
      assert.deepEqual(outcome.rows, [['311', 'loaded'], ['312', null]],
        'values stay string-encoded; SQL NULL becomes null');
    }
    const polls = fake.polls();
    assert.equal(polls.length, 3);
    for (const p of polls) {
      assert.equal(p.raw, STATUS_URL,
        'polling must GET exactly the statementStatusUrl the server returned, query included');
      checkCommonHeaders(p, false);
    }
    assert.deepEqual(waits.map((w) => w.ms), [POLL_MS, POLL_MS, POLL_MS],
      'every wait goes through the injected wait() at the pinned interval');
    assert.ok(waits.every((w) => w.signalOk));
    assert.equal(fake.cancels().length, 0);
  } finally {
    await fake.close();
  }
});

test('injected deadline cancels the statement and verifies the terminal state', async () => {
  const { fake, controller, waits } = await harness();
  try {
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 422, body: canceledFailure() }); // post-cancel verification
    const outcome = await controller.execute(
      { statement: 'call etl.compact_history()' },
      { deadlineMs: 2500 },
    );

    assert.equal(outcome.kind, 'cancelled');
    if (outcome.kind === 'cancelled') {
      assert.equal(outcome.statementHandle, H);
      assert.equal(outcome.code, '000604', 'the Snowflake cancel code must be preserved');
      assert.equal(outcome.sqlState, '57014', 'the cancel sqlState must be preserved');
      assert.equal(outcome.message, 'SQL execution canceled.');
    }

    const cancels = fake.cancels();
    assert.equal(cancels.length, 1, 'exactly one cancel request');
    const c = cancels[0];
    checkCommonHeaders(c, false);
    assert.match(c.params['requestId'] ?? '', UUID_RE, 'cancel carries its own requestId');
    assert.notEqual(c.params['requestId'], '00000000-0000-4000-8000-000000000001',
      'the cancel requestId must be fresh, not the submission id');

    assert.equal(fake.polls().length, 4, '3 live polls + 1 terminal-state verification');
    const order = fake.requests.map((r) => `${r.method} ${r.path}`);
    assert.equal(order[order.length - 2], `POST ${BASE_PATH}/${H}/cancel`);
    assert.equal(order[order.length - 1], `GET ${BASE_PATH}/${H}`,
      'the terminal state must be verified with a status GET after the cancel');
    assert.deepEqual(waits.map((w) => w.ms), [POLL_MS, POLL_MS, POLL_MS],
      'the expired deadline must be noticed before scheduling a fourth wait');
  } finally {
    await fake.close();
  }
});

test('an abort signal firing mid-wait cancels without another live poll', async () => {
  const ctrl = new AbortController();
  const { fake, controller } = await harness({
    onWait: (call) => {
      if (call === 2) ctrl.abort();
    },
  });
  try {
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 422, body: canceledFailure() }); // post-cancel verification
    const outcome = await controller.execute(
      { statement: 'call etl.compact_history()' },
      { signal: ctrl.signal },
    );

    assert.equal(outcome.kind, 'cancelled');
    if (outcome.kind === 'cancelled') {
      assert.equal(outcome.code, '000604');
      assert.equal(outcome.sqlState, '57014');
      assert.equal(outcome.statementHandle, H);
    }
    assert.equal(fake.cancels().length, 1);
    assert.equal(fake.polls().length, 2,
      'after the signal fires: no more live polls, just the terminal-state verification');
  } finally {
    await fake.close();
  }
});

test('a statement that completes at the deadline wins the race — no cancel', async () => {
  const { fake, controller } = await harness();
  try {
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 200, body: resultBody() });
    const outcome = await controller.execute(
      { statement: 'select count(*) from etl.batches' },
      { deadlineMs: 1500 },
    );
    assert.equal(outcome.kind, 'succeeded',
      'a 200 seen while cancelling was still pending must be reported as success');
    assert.equal(fake.cancels().length, 0,
      'never cancel a statement after seeing its terminal success');
  } finally {
    await fake.close();
  }
});

test('a SQL failure surfaces the full Snowflake identity', async () => {
  const { fake, controller } = await harness();
  try {
    fake.pollPlan.push({ status: 202, body: queryStatus() });
    fake.pollPlan.push({ status: 422, body: compileFailure() });
    await assert.rejects(
      controller.execute({ statement: 'select * form etl.batches' }, {}),
      (err: unknown) => {
        assert.ok(err instanceof StatementFailedError);
        assert.equal(err.code, '001003');
        assert.equal(err.sqlState, '42000');
        assert.equal(err.statementHandle, H);
        assert.match(err.message, /001003/);
        assert.match(err.message, /42000/);
        assert.ok(!err.message.includes(TOKEN), 'token leaked into the error');
        return true;
      },
    );
    assert.equal(fake.cancels().length, 0, 'a failed statement must not be cancelled');
  } finally {
    await fake.close();
  }
});

test('auth failures never leak the token', async () => {
  const { fake, controller } = await harness();
  try {
    fake.submitPlan.push({ status: 401, body: { message: 'Authorization token has expired.' } });
    await assert.rejects(
      controller.execute({ statement: 'select 1' }, {}),
      (err: unknown) => {
        assert.ok(err instanceof SqlApiError);
        assert.equal((err as SqlApiError).status, 401);
        assert.ok(!(err as Error).message.includes(TOKEN), 'token leaked into the error');
        return true;
      },
    );
  } finally {
    await fake.close();
  }
});

test('protected research fixtures are intact and first-party', () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(SOURCES.research.official_sources.length >= 2,
    'at least two official sources required');
  for (const src of SOURCES.research.official_sources) {
    assert.match(src.url, /^https:\/\/docs\.snowflake\.com\//,
      'sources must be first-party Snowflake documentation pages');
    assert.ok(src.used_for.length > 0);
  }
  assert.ok(SOURCES.verified_facts.length >= 4);
  assert.equal(CONTRACT.base_path, '/api/v2/statements');
  assert.equal(CONTRACT.submit.query.async, 'always true for this controller');
  assert.equal(CONTRACT.cancel.success.code, '000604');
  assert.equal(CONTRACT.cancel.success.sqlState, '57014');
  assert.equal(CONTRACT.polling.interval_ms, 1000);
});
