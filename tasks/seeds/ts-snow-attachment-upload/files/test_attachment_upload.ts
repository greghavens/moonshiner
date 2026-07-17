// Acceptance tests for the ServiceNow attachment uploader.
//
// Spins up a loopback fake instance implementing the Attachment API subset
// pinned in docs/contract.json. No vendor network, no real credentials.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';

import {
  AttachmentClient,
  SnowApiError,
  RateLimitError,
  AttachmentVerificationError,
} from './attachment_client.ts';

const CONTRACT = JSON.parse(
  readFileSync(new URL('./docs/contract.json', import.meta.url), 'utf8'));
const SOURCES = JSON.parse(
  readFileSync(new URL('./docs/official_sources.json', import.meta.url), 'utf8'));

const USERNAME = 'attach.bot';
const PASSWORD = 'dummy-cred-77e0b2'; // dummy; must never leak or be logged
const EXPECTED_AUTH =
  'Basic ' + Buffer.from(`${USERNAME}:${PASSWORD}`).toString('base64');

const UPLOAD_PATH = CONTRACT.upload.path as string;
const LIST_PATH = CONTRACT.metadata_list.path as string;
const MAX_RETRIES = CONTRACT.retry_policy.rate_limit.max_retries as number;

const INCIDENT_SYS_ID = 'a'.repeat(32);

interface StoredAttachment {
  sys_id: string;
  file_name: string;
  table_name: string;
  table_sys_id: string;
  size_bytes: string;
  content_type: string;
  download_link: string;
  bytes: Buffer;
}

interface Part {
  name: string;
  filename?: string;
  contentType?: string;
  data: Buffer;
}

interface Recorded {
  method: string;
  path: string;
  rawUrl: string;
  params: Record<string, string>;
  headers: http.IncomingHttpHeaders;
  parts?: Part[];
}

interface Fault {
  status: number;
  headers?: Record<string, string>;
  store?: boolean;        // persist the attachment before failing (ambiguous)
  tamperSize?: string;    // report a wrong size_bytes on an otherwise-201
}

function envelope(message: string, detail: string): string {
  return JSON.stringify({ error: { message, detail }, status: 'failure' });
}

function parseMultipart(body: Buffer, contentType: string): Part[] {
  const m = /^multipart\/form-data;\s*boundary=(?:"([^"]+)"|([^;]+))\s*$/i
    .exec(contentType);
  if (!m) throw new Error(`upload Content-Type is not multipart/form-data with a boundary: ${contentType}`);
  const boundary = (m[1] ?? m[2]).trim();
  const delim = Buffer.from(`--${boundary}`);
  if (!body.subarray(0, delim.length).equals(delim)) {
    throw new Error('multipart body must begin with the boundary delimiter');
  }
  const parts: Part[] = [];
  let pos = delim.length;
  for (;;) {
    const marker = body.subarray(pos, pos + 2).toString('latin1');
    if (marker === '--') return parts; // closing delimiter
    if (marker !== '\r\n') throw new Error('boundary must be followed by CRLF or the closing --');
    pos += 2;
    const headerEnd = body.indexOf('\r\n\r\n', pos);
    if (headerEnd < 0) throw new Error('part headers not terminated by a blank line');
    const headerText = body.subarray(pos, headerEnd).toString('utf8');
    const next = body.indexOf(delim, headerEnd + 4);
    if (next < 0) throw new Error('part not terminated by the boundary delimiter');
    if (!body.subarray(next - 2, next).equals(Buffer.from('\r\n'))) {
      throw new Error('part data must end with CRLF before the boundary');
    }
    const headers: Record<string, string> = {};
    for (const line of headerText.split('\r\n')) {
      const idx = line.indexOf(':');
      if (idx > 0) headers[line.slice(0, idx).trim().toLowerCase()] = line.slice(idx + 1).trim();
    }
    const cd = headers['content-disposition'] ?? '';
    const nameMatch = /name="([^"]*)"/.exec(cd);
    if (!/^form-data\b/.test(cd) || !nameMatch) {
      throw new Error(`part is missing a form-data Content-Disposition name: ${cd}`);
    }
    parts.push({
      name: nameMatch[1],
      filename: /filename="([^"]*)"/.exec(cd)?.[1],
      contentType: headers['content-type'],
      data: Buffer.from(body.subarray(headerEnd + 4, next - 2)),
    });
    pos = next + delim.length;
  }
}

class FakeInstance {
  attachments: StoredAttachment[] = [];
  requests: Recorded[] = [];
  faults: Fault[] = [];
  alwaysFault: Fault | null = null;
  private counter = 0;
  private server: http.Server;
  baseUrl = '';

  constructor() {
    this.server = http.createServer((req, res) => this.dispatch(req, res));
  }

  listen(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(0, '127.0.0.1', () => {
        const addr = this.server.address() as AddressInfo;
        this.baseUrl = `http://127.0.0.1:${addr.port}`;
        resolve();
      });
    });
  }

  close(): Promise<void> {
    return new Promise((resolve) => this.server.close(() => resolve()));
  }

  postCount(): number {
    return this.requests.filter((r) => r.method === 'POST').length;
  }

  sequence(): string[] {
    return this.requests.map((r) => `${r.method} ${r.path}`);
  }

  private send(res: http.ServerResponse, status: number, body = '',
               headers: Record<string, string> = {}) {
    res.writeHead(status, { 'Content-Type': 'application/json', ...headers });
    res.end(body);
  }

  private store(parts: Part[]): StoredAttachment {
    const field = (n: string) => parts.find((p) => p.name === n && !p.filename);
    const file = parts.find((p) => p.name === 'uploadFile');
    const tableName = field('table_name')?.data.toString('utf8');
    const tableSysId = field('table_sys_id')?.data.toString('utf8');
    if (!tableName || !tableSysId) throw new Error('table_name and table_sys_id form fields are mandatory');
    if (!file || !file.filename) throw new Error('uploadFile part with a filename is mandatory');
    if (!file.contentType) throw new Error('the uploadFile part must declare the file Content-Type');
    this.counter += 1;
    const sysId = this.counter.toString(16).padStart(32, '0');
    const att: StoredAttachment = {
      sys_id: sysId,
      file_name: file.filename,
      table_name: tableName,
      table_sys_id: tableSysId,
      size_bytes: String(file.data.length),
      content_type: file.contentType,
      download_link: `${this.baseUrl}/api/now/attachment/${sysId}/file`,
      bytes: file.data,
    };
    this.attachments.push(att);
    return att;
  }

  private dispatch(req: http.IncomingMessage, res: http.ServerResponse) {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      const url = new URL(req.url ?? '/', this.baseUrl);
      const rec: Recorded = {
        method: req.method ?? '',
        path: url.pathname,
        rawUrl: req.url ?? '',
        params: Object.fromEntries(url.searchParams),
        headers: req.headers,
      };
      this.requests.push(rec);

      try {
        if (req.headers.authorization !== EXPECTED_AUTH) {
          this.send(res, 401, envelope('User Not Authenticated',
            'Required to provide Auth information'));
          return;
        }
        if (rec.method === 'POST' && rec.path === UPLOAD_PATH) {
          const parts = parseMultipart(body, req.headers['content-type'] ?? '');
          rec.parts = parts;
          const fault = this.alwaysFault ?? this.faults.shift() ?? null;
          if (fault) {
            if (fault.store) this.store(parts);
            this.send(res, fault.status,
              envelope('Operation Failed', `injected fault ${fault.status}`),
              fault.headers ?? {});
            return;
          }
          const att = this.store(parts);
          const { bytes: _bytes, ...meta } = att;
          let result: Record<string, string> = meta;
          if (this.tamper !== null) {
            result = { ...meta, size_bytes: this.tamper };
            this.tamper = null;
          }
          this.send(res, 201, JSON.stringify({ result }),
            { Location: `/api/now/attachment/${att.sys_id}` });
          return;
        }
        if (rec.method === 'GET' && rec.path === LIST_PATH) {
          const query = rec.params['sysparm_query'] ?? '';
          const conds = query ? query.split('^').map((t) => {
            const i = t.indexOf('=');
            if (i < 0) throw new Error(`unsupported query term ${t}`);
            return [t.slice(0, i), t.slice(i + 1)] as const;
          }) : [];
          const matched = this.attachments.filter((a) =>
            conds.every(([f, v]) => (a as unknown as Record<string, string>)[f] === v));
          const result = matched.map(({ bytes, ...meta }) => meta);
          this.send(res, 200, JSON.stringify({ result }),
            { 'X-Total-Count': String(matched.length) });
          return;
        }
        this.send(res, 400, envelope('Unsupported operation',
          `${rec.method} ${rec.path}`));
      } catch (err) {
        this.send(res, 400, envelope('Failed to create the attachment',
          err instanceof Error ? err.message : String(err)));
      }
    });
  }

  tamper: string | null = null;
}

async function fresh() {
  const inst = new FakeInstance();
  await inst.listen();
  const delays: number[] = [];
  const client = new AttachmentClient({
    instanceUrl: inst.baseUrl,
    username: USERNAME,
    password: PASSWORD,
    delay: async (ms: number) => { delays.push(ms); },
    maxRetries: MAX_RETRIES,
  });
  return { inst, client, delays };
}

function bundleBytes(): Buffer {
  const chunks: Buffer[] = [];
  for (let i = 0; i < 4; i++) {
    const b = Buffer.alloc(256);
    for (let j = 0; j < 256; j++) b[j] = j;
    chunks.push(b);
  }
  // hostile payload: CRLFs and a boundary-looking run inside the binary body
  chunks.push(Buffer.from('\r\n--pseudo-boundary--\r\n', 'latin1'));
  return Buffer.concat(chunks);
}

const FILE_NAME = 'diagnostics.tar.gz';
const CONTENT_TYPE = 'application/gzip';

function uploadReq(content: Buffer, fileName = FILE_NAME) {
  return {
    tableName: 'incident',
    tableSysId: INCIDENT_SYS_ID,
    fileName,
    content: new Uint8Array(content),
    contentType: CONTENT_TYPE,
  };
}

test('multipart upload matches the documented Attachment API contract', async () => {
  const { inst, client } = await fresh();
  try {
    const content = bundleBytes();
    const record = await client.upload(uploadReq(content));

    const posts = inst.requests.filter((r) => r.method === 'POST');
    assert.equal(posts.length, 1, 'exactly one POST for a fresh upload');
    const post = posts[0];
    assert.equal(post.path, UPLOAD_PATH, 'must use the documented multipart upload endpoint');
    assert.match(String(post.headers['content-type']),
      /^multipart\/form-data;\s*boundary=.+/,
      'request Content-Type must be multipart/form-data with a boundary');
    assert.equal(post.headers.accept, 'application/json',
      'every request must send Accept: application/json');
    assert.equal(post.headers.authorization, EXPECTED_AUTH,
      'every request must carry Basic credentials');

    const parts = post.parts!;
    const names = parts.map((p) => p.name).sort();
    assert.deepEqual(names, ['table_name', 'table_sys_id', 'uploadFile'],
      'exactly the documented form parts must be present');
    const tableName = parts.find((p) => p.name === 'table_name')!;
    const tableSysId = parts.find((p) => p.name === 'table_sys_id')!;
    const file = parts.find((p) => p.name === 'uploadFile')!;
    assert.equal(tableName.data.toString('utf8'), 'incident');
    assert.equal(tableSysId.data.toString('utf8'), INCIDENT_SYS_ID);
    assert.equal(file.filename, FILE_NAME, 'file part must carry the attachment file name');
    assert.equal(file.contentType, CONTENT_TYPE,
      'file part must declare the file MIME type, not the form MIME type');
    assert.equal(Buffer.compare(file.data, content), 0,
      'uploaded bytes must be byte-identical (no encoding mangling)');

    assert.match(record.sysId, /^[0-9a-f]{32}$/, 'sys_id must be surfaced');
    assert.equal(record.fileName, FILE_NAME);
    assert.equal(record.tableName, 'incident');
    assert.equal(record.tableSysId, INCIDENT_SYS_ID);
    assert.equal(record.sizeBytes, content.length,
      'size_bytes arrives as a string and must be decoded to a number');
    assert.equal(record.contentType, CONTENT_TYPE);
    assert.equal(record.downloadLink,
      `${inst.baseUrl}/api/now/attachment/${record.sysId}/file`);
    assert.equal(inst.attachments.length, 1);
  } finally {
    await inst.close();
  }
});

test('duplicate correlation key returns the existing attachment without a second POST', async () => {
  const { inst, client } = await fresh();
  try {
    const content = bundleBytes();
    const first = await client.upload(uploadReq(content));
    const again = await client.upload(uploadReq(content));
    assert.equal(again.sysId, first.sysId, 'the existing attachment must be returned');
    assert.equal(inst.attachments.length, 1, 'no duplicate attachment may be created');
    assert.equal(inst.postCount(), 1, 'the second call must not POST at all');

    const lookups = inst.requests.filter((r) => r.method === 'GET');
    assert.ok(lookups.length >= 1, 'upload() must correlate before posting');
    for (const g of lookups) {
      assert.equal(g.path, LIST_PATH);
      assert.equal(g.params['sysparm_query'],
        `table_name=incident^table_sys_id=${INCIDENT_SYS_ID}^file_name=${FILE_NAME}`,
        'correlation query must pin table_name, table_sys_id and file_name');
      assert.ok(g.rawUrl.includes('%5E'),
        'the ^ conjunctions in sysparm_query must be percent-encoded on the wire');
    }
  } finally {
    await inst.close();
  }
});

test('ambiguous success (5xx after store) recovers by correlation, never re-POSTs', async () => {
  const { inst, client } = await fresh();
  try {
    const content = bundleBytes();
    inst.faults.push({ status: 502, store: true });
    const record = await client.upload(uploadReq(content));
    assert.equal(inst.attachments.length, 1,
      'exactly one attachment must exist after the ambiguous 502');
    assert.equal(inst.postCount(), 1,
      'the client must not POST again after an ambiguous success');
    assert.equal(record.sysId, inst.attachments[0].sys_id,
      'the recovered record must be the one the interrupted POST created');
    assert.equal(record.sizeBytes, content.length,
      'recovery must verify size_bytes against the uploaded byte count');
    const seq = inst.sequence();
    assert.equal(seq[seq.length - 1], `GET ${LIST_PATH}`,
      'the last step of ambiguous recovery is the correlation re-query');
  } finally {
    await inst.close();
  }
});

test('true 5xx failure (nothing stored) re-queries first, then retries the POST', async () => {
  const { inst, client } = await fresh();
  try {
    const content = bundleBytes();
    inst.faults.push({ status: 503, store: false });
    const record = await client.upload(uploadReq(content));
    assert.equal(inst.attachments.length, 1, 'the retry must succeed exactly once');
    assert.equal(inst.postCount(), 2, 'failed POST plus one retry');
    assert.equal(record.sizeBytes, content.length);
    const seq = inst.sequence();
    const firstPost = seq.indexOf(`POST ${UPLOAD_PATH}`);
    const secondPost = seq.lastIndexOf(`POST ${UPLOAD_PATH}`);
    assert.ok(seq.slice(firstPost + 1, secondPost).includes(`GET ${LIST_PATH}`),
      'a correlation re-query must happen between the failed POST and the retry');
  } finally {
    await inst.close();
  }
});

test('429 honors Retry-After via the injected delay and retries directly', async () => {
  const { inst, client, delays } = await fresh();
  try {
    const content = bundleBytes();
    inst.faults.push({ status: 429, headers: { 'Retry-After': '3' } });
    const record = await client.upload(uploadReq(content));
    assert.equal(record.fileName, FILE_NAME);
    assert.deepEqual(delays, [3000],
      'client must wait Retry-After seconds (as ms) exactly once');
    assert.equal(inst.postCount(), 2, 'rejected POST plus one direct retry');
    const posts = inst.sequence().filter((s) => s.startsWith('POST'));
    assert.equal(posts.length, 2);
    const between = inst.sequence().slice(
      inst.sequence().indexOf(`POST ${UPLOAD_PATH}`) + 1,
      inst.sequence().lastIndexOf(`POST ${UPLOAD_PATH}`));
    assert.ok(!between.includes(`GET ${LIST_PATH}`),
      '429 is rejected-before-processing: retry directly, no correlation re-query');
    assert.equal(inst.attachments.length, 1);
  } finally {
    await inst.close();
  }
});

test('persistent 429 exhausts retries and throws RateLimitError', async () => {
  const { inst, client, delays } = await fresh();
  try {
    inst.alwaysFault = { status: 429, headers: { 'Retry-After': '2' } };
    let thrown: unknown;
    try {
      await client.upload(uploadReq(bundleBytes()));
    } catch (err) {
      thrown = err;
    }
    assert.ok(thrown instanceof RateLimitError, 'must throw RateLimitError');
    assert.ok(thrown instanceof SnowApiError, 'RateLimitError must subclass SnowApiError');
    assert.equal((thrown as RateLimitError).statusCode, 429);
    assert.equal((thrown as RateLimitError).retryAfter, 2,
      '.retryAfter must carry the header value in seconds');
    assert.equal(delays.length, MAX_RETRIES,
      `client must wait ${MAX_RETRIES} times before giving up`);
    assert.equal(inst.postCount(), MAX_RETRIES + 1,
      'original attempt plus max retries, then stop');
    assert.equal(String(thrown).includes(PASSWORD), false,
      'credentials must never appear in exception text');
  } finally {
    await inst.close();
  }
});

test('ServiceNow error envelope surfaces statusCode/message/detail', async () => {
  const { inst, client } = await fresh();
  try {
    inst.faults.push({ status: 413 });
    let thrown: unknown;
    try {
      await client.upload(uploadReq(bundleBytes()));
    } catch (err) {
      thrown = err;
    }
    assert.ok(thrown instanceof SnowApiError, '4xx must throw SnowApiError');
    assert.ok(!(thrown instanceof RateLimitError));
    const err = thrown as SnowApiError;
    assert.equal(err.statusCode, 413);
    assert.equal(err.message.includes('Operation Failed'), true,
      'message must surface the envelope error.message');
    assert.equal(err.detail, 'injected fault 413',
      'detail must surface the envelope error.detail');
    assert.equal(inst.postCount(), 1, 'other 4xx must not be retried');
    assert.equal(String(err).includes(PASSWORD), false,
      'credentials must never appear in exception text');
  } finally {
    await inst.close();
  }
});

test('a 201 whose size_bytes disagrees with the payload is rejected', async () => {
  const { inst, client } = await fresh();
  try {
    inst.tamper = '999';
    let thrown: unknown;
    try {
      await client.upload(uploadReq(bundleBytes()));
    } catch (err) {
      thrown = err;
    }
    assert.ok(thrown instanceof AttachmentVerificationError,
      'response validation must reject a size_bytes mismatch');
  } finally {
    await inst.close();
  }
});

test('list() maps attachment metadata for a record', async () => {
  const { inst, client } = await fresh();
  try {
    await client.upload(uploadReq(bundleBytes(), 'b-second.log'));
    await client.upload(uploadReq(bundleBytes(), 'a-first.log'));
    const listed = await client.list('incident', INCIDENT_SYS_ID);
    assert.equal(listed.length, 2);
    assert.deepEqual(listed.map((r) => r.fileName), ['a-first.log', 'b-second.log'],
      'list() must sort by fileName for deterministic output');
    for (const r of listed) {
      assert.equal(typeof r.sizeBytes, 'number', 'size_bytes must be decoded to a number');
      assert.match(r.sysId, /^[0-9a-f]{32}$/);
      assert.equal(r.tableSysId, INCIDENT_SYS_ID);
    }
  } finally {
    await inst.close();
  }
});

test('protected research fixtures are intact and first-party', () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(SOURCES.research.official_sources.length >= 2,
    'at least two official sources required');
  for (const src of SOURCES.research.official_sources) {
    assert.match(src.url, /^https:\/\//);
    assert.ok(src.url.includes('servicenow.com'), 'sources must be first-party');
    assert.ok(src.used_for.length > 0);
  }
  assert.ok(SOURCES.verified_facts.length >= 4);
  assert.equal(CONTRACT.upload.file_part.name, 'uploadFile');
  assert.equal(CONTRACT.upload.success.status, 201);
  assert.equal(CONTRACT.retry_policy.rate_limit.retry_after_header, 'Retry-After');
  assert.equal(CONTRACT.error_envelope.shape.status, 'failure');
});
