// Acceptance harness: loopback fake Kubernetes API server for the
// server-side-apply ConfigMap contract pinned in docs/contract.json.
// No real cluster, no real credentials. Protected — do not modify.
// Run: node --test test_ssa.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { readFileSync } from 'node:fs';

import { KubeClient, KubeApiError, FieldConflictError } from './client.ts';
import { applyYaml } from './yaml.ts';
import { reconcileConfigMap } from './reconciler.ts';

const TOKEN = 'dummy-bearer-91d3aa'; // dummy; must never leak

// ---------------------------------------------------------------- fake API

interface Recorded {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

interface Scripted {
  status: number;
  body?: string;
  headers?: Record<string, string>;
}

interface Fake {
  base: string;
  requests: Recorded[];
  script: Scripted[];
  close: () => Promise<void>;
}

function statusJSON(code: number, reason: string, message: string, details?: unknown): string {
  const status: Record<string, unknown> = {
    kind: 'Status', apiVersion: 'v1', status: 'Failure', message, reason, code,
  };
  if (details !== undefined) status.details = details;
  return JSON.stringify(status);
}

function conflictJSON(manager: string, fields: string[]): string {
  return statusJSON(
    409, 'Conflict',
    `Apply failed with ${fields.length} conflict${fields.length === 1 ? '' : 's'}: ` +
    fields.map((f) => `conflict with "${manager}": ${f}`).join(', '),
    {
      causes: fields.map((f) => ({
        reason: 'FieldManagerConflict',
        message: `conflict with "${manager}"`,
        field: f,
      })),
    },
  );
}

function cmJSON(name: string, ns: string, data: Record<string, string>,
  labels?: Record<string, string>): string {
  const metadata: Record<string, unknown> = { name, namespace: ns, resourceVersion: '31337' };
  if (labels) metadata.labels = labels;
  return JSON.stringify({ apiVersion: 'v1', kind: 'ConfigMap', metadata, data });
}

function startServer(): Promise<Fake> {
  const requests: Recorded[] = [];
  const script: Scripted[] = [];
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      requests.push({
        method: req.method ?? '',
        url: req.url ?? '',
        headers: req.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      });
      const next = script.shift() ??
        { status: 500, body: statusJSON(500, 'InternalError', 'unscripted request') };
      res.statusCode = next.status;
      res.setHeader('content-type', 'application/json');
      for (const [k, v] of Object.entries(next.headers ?? {})) res.setHeader(k, v);
      res.end(next.body ?? '');
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address() as { port: number };
      resolve({
        base: `http://127.0.0.1:${addr.port}`,
        requests,
        script,
        close: () => new Promise((done) => server.close(() => done())),
      });
    });
  });
}

async function withServer(fn: (f: Fake) => Promise<void>): Promise<void> {
  const f = await startServer();
  try {
    await fn(f);
  } finally {
    await f.close();
  }
}

// ---------------------------------------------------------------- fixtures

const desired = {
  namespace: 'prod',
  name: 'web-cache',
  labels: { app: 'web', tier: 'edge' },
  data: { 'flush.interval': '30s', mode: 'lru' },
};

const desiredYaml =
  'apiVersion: v1\n' +
  'kind: ConfigMap\n' +
  'metadata:\n' +
  '  name: web-cache\n' +
  '  namespace: prod\n' +
  '  labels:\n' +
  '    app: "web"\n' +
  '    tier: "edge"\n' +
  'data:\n' +
  '  flush.interval: "30s"\n' +
  '  mode: "lru"\n';

const OPTS = { fieldManager: 'cfg-sync' };

// ------------------------------------------------------------------ tests

test('protected docs fixtures parse and pin the researched contract', () => {
  const contract = JSON.parse(readFileSync('docs/contract.json', 'utf8'));
  const sources = JSON.parse(readFileSync('docs/official_sources.json', 'utf8'));

  assert.equal(sources.research.required, true, 'research provenance is mandatory');
  assert.ok(sources.research.official_sources.length >= 2, 'need at least two official sources');
  for (const src of sources.research.official_sources) {
    const firstParty = src.url.includes('kubernetes.io')
      || src.url.includes('github.com/kubernetes/kubernetes')
      || src.url.includes('githubusercontent.com/kubernetes/kubernetes');
    assert.ok(src.url.startsWith('https://') && firstParty,
      `official source must be first-party Kubernetes: ${src.url}`);
    assert.ok(src.used_for, 'each source records what it was used for');
  }
  assert.ok(sources.verified_facts.length >= 4, 'verified facts must be summarized');

  assert.equal(contract.apply.media_type, 'application/apply-patch+yaml',
    'contract pins the apply media type');
  assert.equal(contract.apply.method, 'PATCH', 'apply is a PATCH');
  assert.equal(contract.apply.path, '/api/v1/namespaces/{namespace}/configmaps/{name}',
    'contract pins the stable core path');
  assert.equal(contract.apply.params.fieldManager, 'required',
    'fieldManager is required for apply');
  assert.equal(contract.conflict.code, 409, 'contract pins the 409 conflict');
  assert.equal(contract.conflict.cause_reason, 'FieldManagerConflict',
    'contract pins the conflict cause reason');
});

test('applyYaml renders the canonical apply configuration', () => {
  assert.equal(applyYaml(desired), desiredYaml);
});

test('applyYaml sorts keys and quotes values that would change type in YAML', () => {
  const yaml = applyYaml({
    namespace: 'prod',
    name: 'edge-flags',
    data: {
      retention: 'true',
      pin: '007',
      note: 'a: b',
      multi: 'line1\nline2',
      quote: 'say "hi"',
      spark: 'fast ⚡',
    },
  });
  assert.equal(yaml,
    'apiVersion: v1\n' +
    'kind: ConfigMap\n' +
    'metadata:\n' +
    '  name: edge-flags\n' +
    '  namespace: prod\n' +
    'data:\n' +
    '  multi: "line1\\nline2"\n' +
    '  note: "a: b"\n' +
    '  pin: "007"\n' +
    '  quote: "say \\"hi\\""\n' +
    '  retention: "true"\n' +
    '  spark: "fast ⚡"\n');
});

test('applyYaml handles empty data and omits absent labels', () => {
  assert.equal(applyYaml({ namespace: 'prod', name: 'placeholder', data: {} }),
    'apiVersion: v1\n' +
    'kind: ConfigMap\n' +
    'metadata:\n' +
    '  name: placeholder\n' +
    '  namespace: prod\n' +
    'data: {}\n');
});

test('applyYaml rejects data keys the API server would reject', () => {
  assert.throws(() => applyYaml({ namespace: 'prod', name: 'x', data: { 'bad key': 'v' } }),
    /bad key/);
  assert.throws(() => applyYaml({ namespace: 'prod', name: 'x', data: { 'evil/key': 'v' } }),
    /evil\/key/);
});

test('applyConfigMap sends a correct server-side apply PATCH', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', desired.data, desired.labels) });
    const client = new KubeClient(f.base, TOKEN);
    const result = await client.applyConfigMap('prod', 'web-cache', desiredYaml, OPTS);

    assert.equal(result.created, false, 'HTTP 200 means the object already existed');
    assert.equal(result.object.metadata.resourceVersion, '31337', 'server object is returned');

    const r = f.requests[0];
    assert.equal(r.method, 'PATCH', 'server-side apply is a PATCH');
    assert.equal(r.url, '/api/v1/namespaces/prod/configmaps/web-cache?fieldManager=cfg-sync',
      'stable core path plus fieldManager, and no force unless requested');
    assert.equal(r.headers['content-type'], 'application/apply-patch+yaml',
      'the apply media type is mandatory');
    assert.equal(r.headers.accept, 'application/json', 'Accept header set');
    assert.equal(r.headers.authorization, `Bearer ${TOKEN}`, 'bearer token sent');
    assert.equal(r.body, desiredYaml, 'the YAML body is sent byte-for-byte');
  });
});

test('applyConfigMap reports HTTP 201 as a create', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 201, body: cmJSON('web-cache', 'prod', desired.data, desired.labels) });
    const client = new KubeClient(f.base, TOKEN);
    const result = await client.applyConfigMap('prod', 'web-cache', desiredYaml, OPTS);
    assert.equal(result.created, true, 'HTTP 201 means apply created the object');
  });
});

test('namespace, name and query values are percent-encoded', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 200, body: cmJSON('cfg?v=1', 'team/blue', {}) });
    const client = new KubeClient(f.base, TOKEN);
    await client.applyConfigMap('team/blue', 'cfg?v=1', 'data: {}\n',
      { fieldManager: 'release tool', force: true });
    const r = f.requests[0];
    assert.ok(r.url.startsWith('/api/v1/namespaces/team%2Fblue/configmaps/cfg%3Fv%3D1?'),
      `path segments must be escaped, got ${r.url}`);
    assert.ok(r.url.includes('fieldManager=release%20tool'),
      `query values use percent-encoding (%20, not +), got ${r.url}`);
    assert.ok(r.url.endsWith('&force=true'), `force=true appended, got ${r.url}`);
  });
});

test('a 409 apply conflict decodes the field conflicts', async () => {
  await withServer(async (f) => {
    f.script.push({
      status: 409,
      body: conflictJSON('legacy-writer', ['.data.retention', '.metadata.labels.tier']),
    });
    const client = new KubeClient(f.base, TOKEN);
    let thrown: unknown;
    try {
      await client.applyConfigMap('prod', 'web-cache', desiredYaml, OPTS);
    } catch (err) {
      thrown = err;
    }
    assert.ok(thrown instanceof FieldConflictError, 'a 409 apply conflict is typed');
    assert.ok(thrown instanceof KubeApiError, 'FieldConflictError extends KubeApiError');
    const conflict = thrown as FieldConflictError;
    assert.equal(conflict.code, 409);
    assert.equal(conflict.reason, 'Conflict');
    assert.deepEqual(conflict.conflicts, [
      { field: '.data.retention', manager: 'legacy-writer' },
      { field: '.metadata.labels.tier', manager: 'legacy-writer' },
    ], 'conflicts carry the field path and the owning manager');
  });
});

test('getConfigMap returns null for 404 and the object for 200', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 404, body: statusJSON(404, 'NotFound', 'configmaps "web-cache" not found') });
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', { mode: 'lru' }) });
    const client = new KubeClient(f.base, TOKEN);
    assert.equal(await client.getConfigMap('prod', 'web-cache'), null,
      'a missing ConfigMap reads as null, not an error');
    const cm = await client.getConfigMap('prod', 'web-cache');
    assert.equal(cm.data.mode, 'lru');
    assert.equal(f.requests[0].method, 'GET');
    assert.equal(f.requests[0].url, '/api/v1/namespaces/prod/configmaps/web-cache');
  });
});

test('reconcile skips the PATCH when the desired projection is unchanged', async () => {
  await withServer(async (f) => {
    f.script.push({
      status: 200,
      body: cmJSON('web-cache', 'prod',
        { ...desired.data, 'someone.elses.key': 'kept' },
        { ...desired.labels, foreign: 'label' }),
    });
    const client = new KubeClient(f.base, TOKEN);
    const outcome = await reconcileConfigMap(client, desired, OPTS);
    assert.equal(outcome.action, 'unchanged',
      'matching data and labels (extra foreign entries allowed) is a no-op');
    assert.equal(f.requests.length, 1, 'exactly one GET and no PATCH');
    assert.equal(f.requests[0].method, 'GET');
  });
});

test('reconcile applies when a desired value differs', async () => {
  await withServer(async (f) => {
    f.script.push({
      status: 200,
      body: cmJSON('web-cache', 'prod', { 'flush.interval': '10s', mode: 'lru' }, desired.labels),
    });
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', desired.data, desired.labels) });
    const client = new KubeClient(f.base, TOKEN);
    const outcome = await reconcileConfigMap(client, desired, OPTS);
    assert.equal(outcome.action, 'configured', 'HTTP 200 apply reports configured');
    assert.equal(f.requests.length, 2, 'GET then PATCH');
    assert.equal(f.requests[1].method, 'PATCH');
    assert.equal(f.requests[1].body, desiredYaml, 'the PATCH body is the rendered apply YAML');
  });
});

test('reconcile creates a missing ConfigMap through apply', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 404, body: statusJSON(404, 'NotFound', 'configmaps "web-cache" not found') });
    f.script.push({ status: 201, body: cmJSON('web-cache', 'prod', desired.data, desired.labels) });
    const client = new KubeClient(f.base, TOKEN);
    const outcome = await reconcileConfigMap(client, desired, OPTS);
    assert.equal(outcome.action, 'created', 'HTTP 201 apply reports created');
    assert.equal(f.requests.length, 2, 'GET then PATCH; apply itself creates the object');
  });
});

test('reconcile surfaces field conflicts without retrying', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', { mode: 'fifo' }) });
    f.script.push({ status: 409, body: conflictJSON('legacy-writer', ['.data.mode']) });
    const client = new KubeClient(f.base, TOKEN);
    const outcome = await reconcileConfigMap(client, desired, OPTS);
    assert.equal(outcome.action, 'conflict', 'an unforced conflict is reported, not thrown');
    assert.deepEqual(outcome.conflicts, [{ field: '.data.mode', manager: 'legacy-writer' }]);
    assert.equal(f.requests.length, 2, 'a conflicted apply must not be retried silently');
  });
});

test('reconcile with force re-acquires the conflicting fields', async () => {
  await withServer(async (f) => {
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', { mode: 'fifo' }) });
    f.script.push({ status: 200, body: cmJSON('web-cache', 'prod', desired.data, desired.labels) });
    const client = new KubeClient(f.base, TOKEN);
    const outcome = await reconcileConfigMap(client, desired, { fieldManager: 'cfg-sync', force: true });
    assert.equal(outcome.action, 'configured');
    assert.ok(f.requests[1].url.includes('force=true'),
      `force must reach the query string, got ${f.requests[1].url}`);
  });
});

test('non-conflict API errors decode the Status body', async () => {
  await withServer(async (f) => {
    f.script.push({
      status: 403,
      body: statusJSON(403, 'Forbidden',
        'configmaps "web-cache" is forbidden: User "system:serviceaccount:ci:sync" cannot patch resource "configmaps"'),
    });
    const client = new KubeClient(f.base, TOKEN);
    let thrown: unknown;
    try {
      await client.applyConfigMap('prod', 'web-cache', desiredYaml, OPTS);
    } catch (err) {
      thrown = err;
    }
    assert.ok(thrown instanceof KubeApiError, 'API failures are typed');
    assert.ok(!(thrown instanceof FieldConflictError), 'a 403 is not a field conflict');
    const apiErr = thrown as KubeApiError;
    assert.equal(apiErr.code, 403);
    assert.equal(apiErr.reason, 'Forbidden');
    assert.ok(apiErr.message.includes('cannot patch'), 'Status message surfaced');
    assert.ok(!apiErr.message.includes(TOKEN), 'credentials never appear in errors');
  });
});

test('redirects are refused so the bearer token never leaves the API server', async () => {
  await withServer(async (evil) => {
    await withServer(async (f) => {
      f.script.push({
        status: 302,
        headers: { location: `${evil.base}/api/v1/namespaces/prod/configmaps/web-cache` },
      });
      const client = new KubeClient(f.base, TOKEN);
      let thrown: unknown;
      try {
        await client.applyConfigMap('prod', 'web-cache', desiredYaml, OPTS);
      } catch (err) {
        thrown = err;
      }
      assert.ok(thrown instanceof KubeApiError, 'a redirect surfaces as an error');
      assert.equal((thrown as KubeApiError).reason, 'Redirect');
      assert.equal((thrown as KubeApiError).code, 302);
      assert.equal(evil.requests.length, 0,
        'the bearer token must never reach a different origin');
    });
  });
});
