import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { createApp } from './app.ts';
import { BookmarkStore } from './store.ts';

interface Reply {
  status: number;
  headers: http.IncomingHttpHeaders;
  text: string;
}

interface App {
  port: number;
  store: BookmarkStore;
  close: () => Promise<void>;
}

async function boot(opts: { maxBodyBytes?: number } = {}): Promise<App> {
  const store = new BookmarkStore();
  const server = createApp({ store, ...opts });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address() as AddressInfo;
  return {
    port,
    store,
    close: async () => {
      server.closeAllConnections();
      server.close();
      await once(server, 'close');
    },
  };
}

function request(
  port: number,
  method: string,
  path: string,
  opts: { body?: string; contentType?: string | null } = {},
): Promise<Reply> {
  return new Promise((resolve, reject) => {
    const headers: Record<string, string> = {};
    if (opts.body !== undefined) {
      headers['content-length'] = String(Buffer.byteLength(opts.body));
      if (opts.contentType !== null) {
        headers['content-type'] = opts.contentType ?? 'application/json';
      }
    }
    const req = http.request({ host: '127.0.0.1', port, method, path, headers, agent: false }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c: Buffer) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode as number,
        headers: res.headers,
        text: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    req.end(opts.body);
  });
}

function json(reply: Reply): Record<string, unknown> {
  assert.match(String(reply.headers['content-type']), /^application\/json\b/,
    `expected a json response, got ${reply.status} ${reply.headers['content-type']}: ${reply.text}`);
  return JSON.parse(reply.text) as Record<string, unknown>;
}

test('an empty store lists an empty envelope', async () => {
  const app = await boot();
  try {
    const res = await request(app.port, 'GET', '/bookmarks');
    assert.equal(res.status, 200);
    assert.deepEqual(json(res), { items: [], count: 0 });
  } finally {
    await app.close();
  }
});

test('create answers 201 with a Location header and the normalized record', async () => {
  const app = await boot();
  try {
    const res = await request(app.port, 'POST', '/bookmarks', {
      body: JSON.stringify({
        url: ' https://nodejs.org/docs ',
        title: ' Node docs ',
        tags: [' Reading ', 'reading', 'DOCS'],
      }),
    });
    assert.equal(res.status, 201);
    assert.equal(res.headers.location, '/bookmarks/bm-1');
    assert.deepEqual(json(res), {
      id: 'bm-1',
      url: 'https://nodejs.org/docs',
      title: 'Node docs',
      tags: ['reading', 'docs'],
    });
  } finally {
    await app.close();
  }
});

test('listing supports a normalized tag filter and counts what it returns', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A', tags: ['docs'] });
    app.store.create({ url: 'https://example.com/b', title: 'B', tags: ['videos'] });
    app.store.create({ url: 'https://example.com/c', title: 'C', tags: ['docs', 'videos'] });
    const all = json(await request(app.port, 'GET', '/bookmarks')) as { items: Array<{ id: string }>; count: number };
    assert.equal(all.count, 3);
    assert.deepEqual(all.items.map((b) => b.id), ['bm-1', 'bm-2', 'bm-3']);
    const docs = json(await request(app.port, 'GET', '/bookmarks?tag=DOCS')) as { items: Array<{ id: string }>; count: number };
    assert.equal(docs.count, 2);
    assert.deepEqual(docs.items.map((b) => b.id), ['bm-1', 'bm-3']);
  } finally {
    await app.close();
  }
});

test('validation failures answer 400 with every bad field', async () => {
  const app = await boot();
  try {
    const res = await request(app.port, 'POST', '/bookmarks', {
      body: JSON.stringify({ url: 'ftp://files.example.com', title: ' ', tags: ['ok', ''], extra: 1 }),
    });
    assert.equal(res.status, 400);
    const body = json(res) as { error: string; fields: Record<string, string> };
    assert.equal(body.error, 'validation');
    assert.deepEqual(Object.keys(body.fields).sort(), ['extra', 'tags', 'title', 'url']);
  } finally {
    await app.close();
  }
});

test('a duplicate url answers 409', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/guide', title: 'first' });
    const res = await request(app.port, 'POST', '/bookmarks', {
      body: JSON.stringify({ url: '  https://example.com/guide ', title: 'second' }),
    });
    assert.equal(res.status, 409);
    assert.deepEqual(json(res), { error: 'duplicate_url', url: 'https://example.com/guide' });
  } finally {
    await app.close();
  }
});

test('fetch by id works and every unknown path variant is a 404', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A' });
    const hit = await request(app.port, 'GET', '/bookmarks/bm-1');
    assert.equal(hit.status, 200);
    assert.equal((json(hit) as { id: string }).id, 'bm-1');
    for (const path of ['/bookmarks/bm-9', '/bookmarks/', '/bookmarks/bm-1/extra', '/health']) {
      const res = await request(app.port, 'GET', path);
      assert.equal(res.status, 404, `expected 404 for ${path}`);
      assert.deepEqual(json(res), { error: 'not_found' });
    }
  } finally {
    await app.close();
  }
});

test('PATCH updates only the provided fields', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A', tags: ['docs'] });
    const res = await request(app.port, 'PATCH', '/bookmarks/bm-1', {
      body: JSON.stringify({ title: 'A, revised' }),
    });
    assert.equal(res.status, 200);
    assert.deepEqual(json(res), {
      id: 'bm-1', url: 'https://example.com/a', title: 'A, revised', tags: ['docs'],
    });
  } finally {
    await app.close();
  }
});

test('PATCH error paths: 404 before validation, 400 for bad patches, 409 for stolen urls', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A' });
    app.store.create({ url: 'https://example.com/b', title: 'B' });

    const missing = await request(app.port, 'PATCH', '/bookmarks/bm-9', {
      body: JSON.stringify({ title: '' }),
    });
    assert.equal(missing.status, 404);

    const unknownField = await request(app.port, 'PATCH', '/bookmarks/bm-2', {
      body: JSON.stringify({ notes: 'nope' }),
    });
    assert.equal(unknownField.status, 400);
    assert.equal((json(unknownField) as { error: string }).error, 'validation');

    const nonObject = await request(app.port, 'PATCH', '/bookmarks/bm-2', {
      body: JSON.stringify(['title', 'B2']),
    });
    assert.equal(nonObject.status, 400);
    assert.deepEqual(json(nonObject), { error: 'invalid_body' });

    const stolen = await request(app.port, 'PATCH', '/bookmarks/bm-2', {
      body: JSON.stringify({ url: 'https://example.com/a' }),
    });
    assert.equal(stolen.status, 409);

    const own = await request(app.port, 'PATCH', '/bookmarks/bm-2', {
      body: JSON.stringify({ url: 'https://example.com/b', title: 'B2' }),
    });
    assert.equal(own.status, 200, 'keeping your own url is not a conflict');
  } finally {
    await app.close();
  }
});

test('DELETE answers an empty 204 once, 404 after, and ids are never reused', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A' });
    const gone = await request(app.port, 'DELETE', '/bookmarks/bm-1');
    assert.equal(gone.status, 204);
    assert.equal(gone.text, '');
    const again = await request(app.port, 'DELETE', '/bookmarks/bm-1');
    assert.equal(again.status, 404);
    const res = await request(app.port, 'POST', '/bookmarks', {
      body: JSON.stringify({ url: 'https://example.com/b', title: 'B' }),
    });
    assert.equal((json(res) as { id: string }).id, 'bm-2');
  } finally {
    await app.close();
  }
});

test('unroutable methods answer 405 with an exact Allow header', async () => {
  const app = await boot();
  try {
    app.store.create({ url: 'https://example.com/a', title: 'A' });
    const put = await request(app.port, 'PUT', '/bookmarks/bm-1', { body: '{}' });
    assert.equal(put.status, 405);
    assert.equal(put.headers.allow, 'GET, PATCH, DELETE');
    assert.deepEqual(json(put), { error: 'method_not_allowed' });
    const del = await request(app.port, 'DELETE', '/bookmarks');
    assert.equal(del.status, 405);
    assert.equal(del.headers.allow, 'GET, POST');
  } finally {
    await app.close();
  }
});

test('writes demand a json content type, charset parameter allowed', async () => {
  const app = await boot();
  try {
    const plain = await request(app.port, 'POST', '/bookmarks', {
      body: 'url=https://example.com', contentType: 'text/plain',
    });
    assert.equal(plain.status, 415);
    assert.deepEqual(json(plain), { error: 'unsupported_media_type' });

    const missing = await request(app.port, 'POST', '/bookmarks', {
      body: '{}', contentType: null,
    });
    assert.equal(missing.status, 415);

    const charset = await request(app.port, 'POST', '/bookmarks', {
      body: JSON.stringify({ url: 'https://example.com/a', title: 'A' }),
      contentType: 'application/json; charset=utf-8',
    });
    assert.equal(charset.status, 201);
  } finally {
    await app.close();
  }
});

test('bodies that are not a json object are 400 with a precise error code', async () => {
  const app = await boot();
  try {
    const malformed = await request(app.port, 'POST', '/bookmarks', { body: '{"url": ' });
    assert.equal(malformed.status, 400);
    assert.deepEqual(json(malformed), { error: 'invalid_json' });
    const array = await request(app.port, 'POST', '/bookmarks', { body: '[1,2]' });
    assert.equal(array.status, 400);
    assert.deepEqual(json(array), { error: 'invalid_body' });
  } finally {
    await app.close();
  }
});
