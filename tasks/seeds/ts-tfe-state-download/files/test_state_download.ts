// Acceptance harness for the Terraform Enterprise state-version downloader.
// Runs a loopback fake TFE plus a separate fake archivist host, pinning the
// wire contract in docs/contract.json. No real TFE, no real credentials.
// Protected — do not modify. Run: node --test test_state_download.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';

import { TfeClient, ApiError } from './client.ts';
import {
  downloadHostedState,
  downloadCurrentState,
  NoStateError,
  StateNotReadyError,
  IntegrityError,
} from './download.ts';

const TOKEN = 'dummy-tfe-token-51c2'; // dummy; must never leave the API origin
const ORG = 'acme-platform';
const WS_NAME = 'network-prod';
const WS_ID = 'ws-Kk9YvQFyAurexBmM';
const SV_ID = 'sv-BPvFFrYCqRV6anmA';
const WS_PATH = `/api/v2/organizations/${ORG}/workspaces/${WS_NAME}`;
const SV_PATH = `/api/v2/state-versions/${SV_ID}`;
const OBJ_PATH = '/_archivist/v1/object/obj-9xQ4';

const STATE = Buffer.from(JSON.stringify({
  version: 4,
  terraform_version: '1.9.2',
  serial: 42,
  lineage: 'f3a1c1de-32f7-4d1e-9388-7a5b12aa15c1',
  outputs: {},
  resources: [],
}));

const OUT = path.join(process.cwd(), '.state-out');

function freshOut(): void {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT);
}

interface Recorded {
  method: string;
  path: string;
  hasAuth: boolean;
  auth?: string;
}

interface Scripted {
  status: number;
  headers?: Record<string, string>;
  body?: Buffer | string;
}

interface Fake {
  origin: string;
  reqs: Recorded[];
  routes: Map<string, Scripted>;
}

const NOT_FOUND: Scripted = {
  status: 404,
  headers: { 'content-type': 'application/vnd.api+json' },
  body: JSON.stringify({
    errors: [{
      status: '404',
      title: 'not found',
      detail: 'the requested resource could not be found, or user unauthorized to perform action',
    }],
  }),
};

async function startServer(): Promise<Fake> {
  const reqs: Recorded[] = [];
  const routes = new Map<string, Scripted>();
  const server = http.createServer((req, res) => {
    reqs.push({
      method: req.method ?? '',
      path: req.url ?? '',
      hasAuth: req.headers.authorization !== undefined,
      auth: req.headers.authorization,
    });
    const out = routes.get(`${req.method} ${req.url}`) ?? NOT_FOUND;
    res.writeHead(out.status, out.headers ?? {});
    res.end(out.body ?? '');
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  server.unref();
  const addr = server.address() as { port: number };
  return { origin: `http://127.0.0.1:${addr.port}`, reqs, routes };
}

function doc(body: unknown): Scripted {
  return {
    status: 200,
    headers: { 'content-type': 'application/vnd.api+json' },
    body: JSON.stringify(body),
  };
}

function workspaceDoc(svId: string | null): unknown {
  return {
    data: {
      id: WS_ID,
      type: 'workspaces',
      attributes: { name: WS_NAME },
      relationships: {
        'current-state-version': {
          data: svId === null ? null : { id: svId, type: 'state-versions' },
        },
      },
    },
  };
}

function svDoc(downloadUrl: string, overrides: Record<string, unknown> = {}): unknown {
  return {
    data: {
      id: SV_ID,
      type: 'state-versions',
      attributes: {
        'hosted-state-download-url': downloadUrl,
        'hosted-json-state-download-url': null,
        serial: 42,
        size: STATE.length,
        status: 'finalized',
        'resources-processed': true,
        'terraform-version': '1.9.2',
        ...overrides,
      },
    },
  };
}

// A fully-wired fake TFE whose archivist path lives on the API origin.
async function standardTfe(svOverrides: Record<string, unknown> = {}): Promise<Fake> {
  const tfe = await startServer();
  tfe.routes.set(`GET ${WS_PATH}`, doc(workspaceDoc(SV_ID)));
  tfe.routes.set(`GET ${SV_PATH}`, doc(svDoc(tfe.origin + OBJ_PATH, svOverrides)));
  tfe.routes.set(`GET ${OBJ_PATH}`, { status: 200, body: STATE });
  return tfe;
}

test('resolveWorkspace follows the documented workspace show contract', async () => {
  const tfe = await standardTfe();
  const client = new TfeClient(tfe.origin, TOKEN);
  const ws = await client.resolveWorkspace(ORG, WS_NAME);
  assert.equal(ws.id, WS_ID);
  assert.equal(ws.currentStateVersionId, SV_ID);
  assert.equal(tfe.reqs.length, 1);
  assert.equal(tfe.reqs[0].method, 'GET');
  assert.equal(tfe.reqs[0].path, WS_PATH);
  assert.equal(tfe.reqs[0].auth, `Bearer ${TOKEN}`);
});

test('a workspace that never stored state resolves to null and refuses download', async () => {
  freshOut();
  const tfe = await startServer();
  tfe.routes.set(`GET ${WS_PATH}`, doc(workspaceDoc(null)));
  const client = new TfeClient(tfe.origin, TOKEN);
  const ws = await client.resolveWorkspace(ORG, WS_NAME);
  assert.equal(ws.currentStateVersionId, null);
  await assert.rejects(
    downloadCurrentState(client, ORG, WS_NAME, path.join(OUT, 'x.tfstate')),
    (err: unknown) => err instanceof NoStateError,
  );
  assert.deepEqual(fs.readdirSync(OUT), []);
});

test('getStateVersion decodes the documented attributes', async () => {
  const tfe = await standardTfe();
  const client = new TfeClient(tfe.origin, TOKEN);
  const sv = await client.getStateVersion(SV_ID);
  assert.equal(sv.id, SV_ID);
  assert.equal(sv.status, 'finalized');
  assert.equal(sv.serial, 42);
  assert.equal(sv.size, STATE.length);
  assert.equal(sv.resourcesProcessed, true);
  assert.equal(sv.downloadUrl, tfe.origin + OBJ_PATH);
  assert.equal(sv.jsonDownloadUrl, null);
  const req = tfe.reqs[tfe.reqs.length - 1];
  assert.equal(req.path, SV_PATH);
  assert.equal(req.auth, `Bearer ${TOKEN}`);
});

test('same-origin download keeps auth; a cross-host redirect must drop it', async () => {
  const tfe = await startServer();
  const archivist = await startServer();
  archivist.routes.set('GET /v1/object/obj-9xQ4', { status: 200, body: STATE });
  tfe.routes.set(`GET ${OBJ_PATH}`, {
    status: 302,
    headers: { location: `${archivist.origin}/v1/object/obj-9xQ4` },
  });
  const client = new TfeClient(tfe.origin, TOKEN);
  const bytes = await downloadHostedState(client, tfe.origin + OBJ_PATH);
  assert.deepEqual(Buffer.from(bytes), STATE);
  assert.equal(tfe.reqs.length, 1);
  assert.equal(tfe.reqs[0].auth, `Bearer ${TOKEN}`, 'same-origin hop carries the bearer');
  assert.equal(archivist.reqs.length, 1);
  assert.equal(archivist.reqs[0].hasAuth, false,
    'the bearer token must never be forwarded to a different origin');
});

test('a download URL already on a foreign origin gets no auth at all', async () => {
  const tfe = await startServer();
  const archivist = await startServer();
  archivist.routes.set('GET /v1/object/obj-9xQ4', { status: 200, body: STATE });
  const client = new TfeClient(tfe.origin, TOKEN);
  const bytes = await downloadHostedState(client, `${archivist.origin}/v1/object/obj-9xQ4`);
  assert.deepEqual(Buffer.from(bytes), STATE);
  assert.equal(archivist.reqs.length, 1);
  assert.equal(archivist.reqs[0].hasAuth, false);
});

test('more than 3 redirects is an error', async () => {
  const tfe = await startServer();
  for (let i = 1; i <= 4; i++) {
    tfe.routes.set(`GET /hop${i}`, {
      status: 302,
      headers: { location: `${tfe.origin}/hop${i + 1}` },
    });
  }
  tfe.routes.set('GET /hop5', { status: 200, body: STATE });
  const client = new TfeClient(tfe.origin, TOKEN);
  await assert.rejects(
    downloadHostedState(client, `${tfe.origin}/hop1`),
    /redirect/i,
  );
});

test('downloadCurrentState writes the verified bytes atomically', async () => {
  freshOut();
  const tfe = await standardTfe();
  const client = new TfeClient(tfe.origin, TOKEN);
  const dest = path.join(OUT, 'network-prod.tfstate');
  const result = await downloadCurrentState(client, ORG, WS_NAME, dest);

  assert.equal(result.stateVersionId, SV_ID);
  assert.equal(result.serial, 42);
  assert.equal(result.bytesWritten, STATE.length);
  assert.equal(result.path, dest);
  assert.deepEqual(fs.readFileSync(dest), STATE);
  assert.deepEqual(fs.readdirSync(OUT), ['network-prod.tfstate'],
    'no temporary file may remain next to the destination');

  const paths = tfe.reqs.map((r) => r.path);
  assert.deepEqual(paths, [WS_PATH, SV_PATH, OBJ_PATH],
    'resolve workspace, then state version, then one download');
});

test('a state version that is not finalized is not downloaded', async () => {
  freshOut();
  const tfe = await standardTfe({ status: 'pending' });
  const client = new TfeClient(tfe.origin, TOKEN);
  await assert.rejects(
    downloadCurrentState(client, ORG, WS_NAME, path.join(OUT, 'x.tfstate')),
    (err: unknown) => err instanceof StateNotReadyError,
  );
  assert.equal(tfe.reqs.some((r) => r.path === OBJ_PATH), false,
    'no download request may be made for a pending state version');
  assert.deepEqual(fs.readdirSync(OUT), []);
});

test('a size mismatch is an integrity failure and writes nothing', async () => {
  freshOut();
  const tfe = await standardTfe({ size: STATE.length + 7 });
  const client = new TfeClient(tfe.origin, TOKEN);
  await assert.rejects(
    downloadCurrentState(client, ORG, WS_NAME, path.join(OUT, 'x.tfstate')),
    (err: unknown) => err instanceof IntegrityError && /size/i.test((err as Error).message),
  );
  assert.deepEqual(fs.readdirSync(OUT), [], 'no destination and no temp file');
});

test('a serial mismatch leaves an existing destination byte-identical', async () => {
  freshOut();
  const tfe = await standardTfe({ serial: 43 });
  const client = new TfeClient(tfe.origin, TOKEN);
  const dest = path.join(OUT, 'network-prod.tfstate');
  fs.writeFileSync(dest, 'OLD STATE CONTENTS');
  await assert.rejects(
    downloadCurrentState(client, ORG, WS_NAME, dest),
    (err: unknown) => err instanceof IntegrityError && /serial/i.test((err as Error).message),
  );
  assert.equal(fs.readFileSync(dest, 'utf8'), 'OLD STATE CONTENTS');
  assert.deepEqual(fs.readdirSync(OUT), ['network-prod.tfstate']);
});

test('absent size metadata skips the size check but still verifies serial', async () => {
  freshOut();
  const tfe = await standardTfe({ size: null });
  const client = new TfeClient(tfe.origin, TOKEN);
  const dest = path.join(OUT, 'no-size.tfstate');
  const result = await downloadCurrentState(client, ORG, WS_NAME, dest);
  assert.equal(result.serial, 42);
  assert.deepEqual(fs.readFileSync(dest), STATE);
});

test('JSON:API error documents decode with the 404 masking wording intact', async () => {
  const tfe = await startServer(); // every route answers the 404 document
  const client = new TfeClient(tfe.origin, TOKEN);
  await assert.rejects(
    client.resolveWorkspace(ORG, WS_NAME),
    (err: unknown) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.statusCode, 404);
      assert.equal(err.errors.length, 1);
      assert.equal(err.errors[0].title, 'not found');
      assert.match(err.errors[0].detail, /user unauthorized to perform action/);
      assert.match((err as Error).message, /user unauthorized/);
      return true;
    },
  );
});
