// Wire-level checks: the error paths must leave each connection in a defined
// state. Driven over a raw socket so keep-alive reuse is actually observable.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as net from 'node:net';
import type { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { createApp } from './app.ts';
import { BookmarkStore } from './store.ts';

const IDLE_LIMIT_MS = 5000;

interface WireResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}

class WireClient {
  timedOut = false;
  private sock: net.Socket;
  private buf: Buffer = Buffer.alloc(0);
  private ended = false;
  private wakers: Array<() => void> = [];

  constructor(sock: net.Socket) {
    this.sock = sock;
    sock.on('data', (d: Buffer) => {
      this.buf = Buffer.concat([this.buf, d]);
      this.wake();
    });
    sock.on('close', () => {
      this.ended = true;
      this.wake();
    });
    sock.on('error', () => {
      this.ended = true;
      this.wake();
    });
    sock.setTimeout(IDLE_LIMIT_MS, () => {
      this.timedOut = true;
      sock.destroy();
    });
  }

  static async connect(port: number): Promise<WireClient> {
    const sock = net.connect(port, '127.0.0.1');
    await once(sock, 'connect');
    return new WireClient(sock);
  }

  send(text: string): void {
    this.sock.write(text);
  }

  get closed(): boolean {
    return this.ended;
  }

  destroy(): void {
    this.sock.destroy();
  }

  private wake(): void {
    for (const waker of this.wakers.splice(0)) waker();
  }

  private waitChange(): Promise<void> {
    return new Promise((resolve) => this.wakers.push(resolve));
  }

  /** Parse exactly one response off the stream (Content-Length framing). */
  async response(): Promise<WireResponse> {
    let sep = this.buf.indexOf('\r\n\r\n');
    while (sep === -1) {
      assert.ok(!this.ended, 'connection closed before a full response arrived');
      await this.waitChange();
      sep = this.buf.indexOf('\r\n\r\n');
    }
    const headText = this.buf.subarray(0, sep).toString('latin1');
    const [statusLine, ...headerLines] = headText.split('\r\n');
    const m = /^HTTP\/1\.1 (\d{3})/.exec(statusLine);
    assert.ok(m, `status line, got ${JSON.stringify(statusLine)}`);
    const headers: Record<string, string> = {};
    for (const line of headerLines) {
      const colon = line.indexOf(':');
      headers[line.slice(0, colon).trim().toLowerCase()] = line.slice(colon + 1).trim();
    }
    const status = Number(m[1]);
    const length = status === 204 ? 0 : Number(headers['content-length'] ?? NaN);
    assert.ok(Number.isFinite(length), `response carries a content-length (status ${status})`);
    const bodyStart = sep + 4;
    while (this.buf.length < bodyStart + length) {
      assert.ok(!this.ended, 'connection closed mid-body');
      await this.waitChange();
    }
    const body = this.buf.subarray(bodyStart, bodyStart + length).toString('utf8');
    this.buf = Buffer.from(this.buf.subarray(bodyStart + length));
    return { status, headers, body };
  }

  /** Resolve once the server has closed the connection. */
  async waitClose(): Promise<void> {
    while (!this.ended) {
      await this.waitChange();
    }
  }
}

interface App {
  port: number;
  close: () => Promise<void>;
}

async function boot(opts: { maxBodyBytes?: number } = {}): Promise<App> {
  const server = createApp({ store: new BookmarkStore(), ...opts });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address() as AddressInfo;
  return {
    port,
    close: async () => {
      server.closeAllConnections();
      server.close();
      await once(server, 'close');
    },
  };
}

function get(path: string): string {
  return `GET ${path} HTTP/1.1\r\nHost: bookmarks.test\r\n\r\n`;
}

function post(path: string, body: string, extra: string[] = []): string {
  const head = [
    `POST ${path} HTTP/1.1`,
    'Host: bookmarks.test',
    'Content-Type: application/json',
    `Content-Length: ${Buffer.byteLength(body)}`,
    ...extra,
  ];
  return head.join('\r\n') + '\r\n\r\n' + body;
}

test('two requests ride the same keep-alive connection', async () => {
  const app = await boot();
  const client = await WireClient.connect(app.port);
  try {
    client.send(get('/bookmarks'));
    const first = await client.response();
    assert.equal(first.status, 200);
    assert.notEqual(first.headers.connection, 'close');
    client.send(get('/bookmarks'));
    const second = await client.response();
    assert.equal(second.status, 200);
    assert.equal(client.timedOut, false);
  } finally {
    client.destroy();
    await app.close();
  }
});

test('a malformed json body gets a 400 and the connection stays usable', async () => {
  const app = await boot();
  const client = await WireClient.connect(app.port);
  try {
    client.send(post('/bookmarks', '{"url": "https://example.com", '));
    const bad = await client.response();
    assert.equal(bad.status, 400);
    assert.notEqual(bad.headers.connection, 'close', 'a fully-read bad body must not cost the connection');
    client.send(get('/bookmarks'));
    const follow = await client.response();
    assert.equal(follow.status, 200);
    assert.equal(client.timedOut, false);
  } finally {
    client.destroy();
    await app.close();
  }
});

test('a declared over-limit body is refused up front and the connection is closed', async () => {
  const app = await boot({ maxBodyBytes: 256 });
  const client = await WireClient.connect(app.port);
  try {
    // Headers only: the server must answer without waiting for 100000 bytes.
    client.send(
      'POST /bookmarks HTTP/1.1\r\nHost: bookmarks.test\r\n' +
      'Content-Type: application/json\r\nContent-Length: 100000\r\n\r\n');
    const res = await client.response();
    assert.equal(res.status, 413);
    assert.equal(res.headers.connection, 'close');
    assert.deepEqual(JSON.parse(res.body), { error: 'payload_too_large', limit: 256 });
    await client.waitClose();
    assert.equal(client.timedOut, false, 'the server itself must close the connection');
  } finally {
    client.destroy();
    await app.close();
  }
});

test('a chunked body that grows past the limit is cut off with 413 and a close', async () => {
  const app = await boot({ maxBodyBytes: 256 });
  const client = await WireClient.connect(app.port);
  try {
    client.send(
      'POST /bookmarks HTTP/1.1\r\nHost: bookmarks.test\r\n' +
      'Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n');
    const piece = 'x'.repeat(128);
    for (let i = 0; i < 3; i++) {
      client.send(`${(128).toString(16)}\r\n${piece}\r\n`); // 384 bytes total, never terminated
    }
    const res = await client.response();
    assert.equal(res.status, 413);
    assert.equal(res.headers.connection, 'close');
    assert.equal((JSON.parse(res.body) as { error: string }).error, 'payload_too_large');
    await client.waitClose();
    assert.equal(client.timedOut, false, 'the server itself must close the connection');
  } finally {
    client.destroy();
    await app.close();
  }
});

test('an unsupported content type is drained politely and the connection survives', async () => {
  const app = await boot();
  const client = await WireClient.connect(app.port);
  try {
    const body = 'url=https://example.com&title=nope';
    client.send(
      'POST /bookmarks HTTP/1.1\r\nHost: bookmarks.test\r\n' +
      `Content-Type: text/plain\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n` + body);
    const res = await client.response();
    assert.equal(res.status, 415);
    client.send(get('/bookmarks'));
    const follow = await client.response();
    assert.equal(follow.status, 200);
    assert.equal(client.timedOut, false);
  } finally {
    client.destroy();
    await app.close();
  }
});

test('a successful create leaves the connection open for the next request', async () => {
  const app = await boot();
  const client = await WireClient.connect(app.port);
  try {
    client.send(post('/bookmarks', JSON.stringify({ url: 'https://example.com/a', title: 'A' })));
    const created = await client.response();
    assert.equal(created.status, 201);
    client.send(get('/bookmarks'));
    const list = await client.response();
    assert.equal(list.status, 200);
    assert.equal((JSON.parse(list.body) as { count: number }).count, 1);
    assert.equal(client.timedOut, false);
  } finally {
    client.destroy();
    await app.close();
  }
});
