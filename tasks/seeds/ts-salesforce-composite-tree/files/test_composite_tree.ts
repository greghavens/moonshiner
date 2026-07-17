// Acceptance tests for the Salesforce relationship writers.
//
// Spins up a loopback fake of the Salesforce REST Composite resources
// (POST /services/data/v67.0/composite and /composite/tree/{sObjectName})
// implementing the contract pinned in docs/contract.json. No vendor network,
// no real credentials.
//
// Covers the existing sObject Tree writer (must keep working) and the new
// cross-record Composite writer.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import type { AddressInfo } from 'node:net';

import {
  SalesforceOrg,
  SalesforceApiError,
  TreeLimitError,
  TreeWriteError,
  insertTree,
} from './tree_client.ts';
import type { TreeRecord } from './tree_client.ts';

import {
  writeLinked,
  CompositeLimitError,
  CompositeRefError,
} from './composite_writer.ts';
import type { CompositeStep } from './composite_writer.ts';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const CONTRACT = JSON.parse(
  fs.readFileSync(path.join(HERE, 'docs', 'contract.json'), 'utf8'));
const SOURCES = JSON.parse(
  fs.readFileSync(path.join(HERE, 'docs', 'official_sources.json'), 'utf8'));

const TOKEN = 'dummy-session-token-91c4e7'; // dummy; must never leak
const V = CONTRACT.api_version as string;   // v67.0

interface Recorded {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
  body: unknown;
}

interface Scripted {
  status: number;
  body: unknown;
}

class FakeOrgServer {
  readonly requests: Recorded[] = [];
  private readonly script: Scripted[] = [];
  private server!: http.Server;
  baseUrl = '';

  async start(): Promise<this> {
    this.server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        this.requests.push({
          method: req.method ?? '',
          path: req.url ?? '',
          headers: req.headers,
          body: raw.length > 0 ? JSON.parse(raw) : null,
        });
        const next = this.script.shift();
        if (!next) {
          res.writeHead(500, { 'content-type': 'application/json' });
          res.end(JSON.stringify([{
            message: 'fake org: no scripted response left',
            errorCode: 'UNKNOWN_EXCEPTION',
          }]));
          return;
        }
        res.writeHead(next.status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(next.body));
      });
    });
    await new Promise<void>((resolve) => {
      this.server.listen(0, '127.0.0.1', resolve);
    });
    const addr = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${addr.port}`;
    return this;
  }

  reply(status: number, body: unknown): void {
    this.script.push({ status, body });
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }

  get last(): Recorded {
    const r = this.requests[this.requests.length - 1];
    assert.ok(r, 'expected at least one recorded request');
    return r;
  }
}

function org(fake: FakeOrgServer): SalesforceOrg {
  return new SalesforceOrg(fake.baseUrl, V, TOKEN);
}

function acct(referenceId: string, name: string, children?: TreeRecord[]): TreeRecord {
  return {
    type: 'Account',
    referenceId,
    fields: { Name: name },
    ...(children ? { children: { Contacts: children } } : {}),
  };
}

// ---------------------------------------------------------------------------
// Existing behavior: sObject Tree writer (regression — this already works)
// ---------------------------------------------------------------------------

test('insertTree posts the documented tree shape and maps ids', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(201, {
      hasErrors: false,
      results: [
        { referenceId: 'acmeHQ', id: '001KB000001aaaAAA' },
        { referenceId: 'cfo', id: '003KB000002bbbAAA' },
        { referenceId: 'acmeWest', id: '001KB000003cccAAA' },
      ],
    });
    const ids = await insertTree(org(fake), 'Account', [
      {
        type: 'Account',
        referenceId: 'acmeHQ',
        fields: { Name: 'Acme HQ', Industry: 'Energy' },
        children: {
          Contacts: [{
            type: 'Contact',
            referenceId: 'cfo',
            fields: { LastName: 'Vance', Email: 'vance@example.com' },
          }],
        },
      },
      { type: 'Account', referenceId: 'acmeWest', fields: { Name: 'Acme West' } },
    ]);

    assert.equal(fake.requests.length, 1, 'exactly one tree call');
    const req = fake.last;
    assert.equal(req.method, 'POST');
    assert.equal(req.path, `/services/data/${V}/composite/tree/Account`,
      'tree endpoint is versioned and object-scoped');
    assert.equal(req.headers.authorization, `Bearer ${TOKEN}`);
    assert.match(String(req.headers['content-type']), /^application\/json/);
    assert.deepEqual(req.body, {
      records: [
        {
          attributes: { type: 'Account', referenceId: 'acmeHQ' },
          Name: 'Acme HQ',
          Industry: 'Energy',
          Contacts: {
            records: [{
              attributes: { type: 'Contact', referenceId: 'cfo' },
              LastName: 'Vance',
              Email: 'vance@example.com',
            }],
          },
        },
        {
          attributes: { type: 'Account', referenceId: 'acmeWest' },
          Name: 'Acme West',
        },
      ],
    }, 'tree request body must match the documented wire shape exactly');
    assert.deepEqual(ids, {
      acmeHQ: '001KB000001aaaAAA',
      cfo: '003KB000002bbbAAA',
      acmeWest: '001KB000003cccAAA',
    });
  } finally {
    await fake.stop();
  }
});

test('insertTree enforces the documented tree limits before any HTTP call', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    const many: TreeRecord[] = [];
    for (let i = 0; i < 201; i += 1) many.push(acct(`a${i}`, `Acct ${i}`));
    await assert.rejects(
      insertTree(org(fake), 'Account', many),
      TreeLimitError,
      '201 records must be rejected locally (limit is 200)');

    let deep: TreeRecord = acct('lvl6', 'Leaf');
    for (let lvl = 5; lvl >= 1; lvl -= 1) deep = acct(`lvl${lvl}`, `L${lvl}`, [deep]);
    await assert.rejects(
      insertTree(org(fake), 'Account', [deep]),
      TreeLimitError,
      'six levels must be rejected locally (limit is five)');

    assert.equal(fake.requests.length, 0,
      'limit violations must never reach the wire');
  } finally {
    await fake.stop();
  }
});

test('insertTree decodes hasErrors rollbacks without leaking partial ids', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(400, {
      hasErrors: true,
      results: [{
        referenceId: 'cfo',
        errors: [{
          statusCode: 'INVALID_EMAIL_ADDRESS',
          message: 'Email: invalid email address: not-an-email',
          fields: ['Email'],
        }],
      }],
    });
    await assert.rejects(
      insertTree(org(fake), 'Account', [
        acct('acmeHQ', 'Acme HQ', [{
          type: 'Contact',
          referenceId: 'cfo',
          fields: { LastName: 'Vance', Email: 'not-an-email' },
        }]),
      ]),
      (err: unknown) => {
        assert.ok(err instanceof TreeWriteError,
          'a hasErrors response must raise TreeWriteError');
        assert.equal(err.errors.length, 1);
        assert.deepEqual(err.errors[0], {
          referenceId: 'cfo',
          statusCode: 'INVALID_EMAIL_ADDRESS',
          message: 'Email: invalid email address: not-an-email',
          fields: ['Email'],
        }, 'per-record error must keep referenceId, statusCode, message, fields');
        return true;
      });
  } finally {
    await fake.stop();
  }
});

// ---------------------------------------------------------------------------
// New feature: cross-record Composite writer
// ---------------------------------------------------------------------------

function threeLinkedSteps(): CompositeStep[] {
  return [
    { refId: 'acmeHQ', sobject: 'Account', fields: { Name: 'Acme HQ' } },
    {
      refId: 'cfo',
      sobject: 'Contact',
      fields: { LastName: 'Vance', AccountId: { $ref: 'acmeHQ' } },
    },
    {
      refId: 'renewal',
      sobject: 'Opportunity',
      fields: {
        Name: 'Acme renewal',
        StageName: 'Prospecting',
        CloseDate: '2026-09-30',
        AccountId: { $ref: 'acmeHQ' },
      },
    },
  ];
}

test('writeLinked builds the documented composite request with @{ref.id} wiring', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(200, {
      compositeResponse: [
        {
          body: { id: '001KB000001dddAAA', success: true, errors: [] },
          httpHeaders: { Location: `/services/data/${V}/sobjects/Account/001KB000001dddAAA` },
          httpStatusCode: 201,
          referenceId: 'acmeHQ',
        },
        {
          body: { id: '003KB000002eeeAAA', success: true, errors: [] },
          httpHeaders: { Location: `/services/data/${V}/sobjects/Contact/003KB000002eeeAAA` },
          httpStatusCode: 201,
          referenceId: 'cfo',
        },
        {
          body: { id: '006KB000003fffAAA', success: true, errors: [] },
          httpHeaders: { Location: `/services/data/${V}/sobjects/Opportunity/006KB000003fffAAA` },
          httpStatusCode: 201,
          referenceId: 'renewal',
        },
      ],
    });

    const result = await writeLinked(org(fake), threeLinkedSteps());

    assert.equal(fake.requests.length, 1, 'one composite call for the whole graph');
    const req = fake.last;
    assert.equal(req.method, 'POST');
    assert.equal(req.path, `/services/data/${V}/composite`,
      'the composite endpoint is versioned');
    assert.equal(req.headers.authorization, `Bearer ${TOKEN}`);
    assert.deepEqual(req.body, {
      allOrNone: true,
      compositeRequest: [
        {
          method: 'POST',
          url: `/services/data/${V}/sobjects/Account`,
          referenceId: 'acmeHQ',
          body: { Name: 'Acme HQ' },
        },
        {
          method: 'POST',
          url: `/services/data/${V}/sobjects/Contact`,
          referenceId: 'cfo',
          body: { LastName: 'Vance', AccountId: '@{acmeHQ.id}' },
        },
        {
          method: 'POST',
          url: `/services/data/${V}/sobjects/Opportunity`,
          referenceId: 'renewal',
          body: {
            Name: 'Acme renewal',
            StageName: 'Prospecting',
            CloseDate: '2026-09-30',
            AccountId: '@{acmeHQ.id}',
          },
        },
      ],
    }, 'composite request body must match the documented wire shape exactly');

    assert.equal(result.ok, true);
    assert.deepEqual(result.ids, {
      acmeHQ: '001KB000001dddAAA',
      cfo: '003KB000002eeeAAA',
      renewal: '006KB000003fffAAA',
    });
    assert.deepEqual(result.errors, {}, 'no errors on a fully successful write');
  } finally {
    await fake.stop();
  }
});

test('writeLinked validates limits and references before any HTTP call', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    const many: CompositeStep[] = [];
    for (let i = 0; i < 26; i += 1) {
      many.push({ refId: `r${i}`, sobject: 'Account', fields: { Name: `A${i}` } });
    }
    await assert.rejects(writeLinked(org(fake), many), CompositeLimitError,
      '26 subrequests must be rejected locally (limit is 25)');

    await assert.rejects(
      writeLinked(org(fake), [
        { refId: 'bad-ref!', sobject: 'Account', fields: { Name: 'X' } },
      ]),
      CompositeRefError,
      'referenceIds allow only letters, numbers and underscores');

    await assert.rejects(
      writeLinked(org(fake), [
        {
          refId: 'contact1',
          sobject: 'Contact',
          fields: { LastName: 'Early', AccountId: { $ref: 'acctLater' } },
        },
        { refId: 'acctLater', sobject: 'Account', fields: { Name: 'Late' } },
      ]),
      CompositeRefError,
      'a $ref must point at an EARLIER step');

    await assert.rejects(
      writeLinked(org(fake), [
        {
          refId: 'contact1',
          sobject: 'Contact',
          fields: { LastName: 'Orphan', AccountId: { $ref: 'nowhere' } },
        },
      ]),
      CompositeRefError,
      'a $ref to an unknown step must be rejected');

    assert.equal(fake.requests.length, 0,
      'validation failures must never reach the wire');
  } finally {
    await fake.stop();
  }
});

test('writeLinked treats an HTTP 200 partial failure as a failure', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(200, {
      compositeResponse: [
        {
          body: { id: '001KB000009aaaAAA', success: true, errors: [] },
          httpHeaders: { Location: `/services/data/${V}/sobjects/Account/001KB000009aaaAAA` },
          httpStatusCode: 201,
          referenceId: 'acmeHQ',
        },
        {
          body: [{
            errorCode: 'REQUIRED_FIELD_MISSING',
            message: 'Required fields are missing: [LastName]',
            fields: ['LastName'],
          }],
          httpHeaders: {},
          httpStatusCode: 400,
          referenceId: 'cfo',
        },
      ],
    });

    const steps: CompositeStep[] = [
      { refId: 'acmeHQ', sobject: 'Account', fields: { Name: 'Acme HQ' } },
      {
        refId: 'cfo',
        sobject: 'Contact',
        fields: { AccountId: { $ref: 'acmeHQ' } },
      },
    ];
    const result = await writeLinked(org(fake), steps, { allOrNone: false });

    assert.equal((fake.last.body as { allOrNone: boolean }).allOrNone, false,
      'allOrNone: false must be passed through');
    assert.equal(result.ok, false,
      'top-level HTTP 200 with a failed subrequest is NOT a success');
    assert.deepEqual(result.ids, { acmeHQ: '001KB000009aaaAAA' },
      'partial-failure mode keeps the ids that did commit');
    assert.deepEqual(result.errors, {
      cfo: [{
        errorCode: 'REQUIRED_FIELD_MISSING',
        message: 'Required fields are missing: [LastName]',
      }],
    }, 'per-record errors are keyed by referenceId');
  } finally {
    await fake.stop();
  }
});

test('writeLinked reports zero ids after an allOrNone rollback', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(200, {
      compositeResponse: [
        {
          body: [{
            errorCode: 'PROCESSING_HALTED',
            message: 'The transaction was rolled back since another operation in the same transaction failed.',
          }],
          httpHeaders: {},
          httpStatusCode: 400,
          referenceId: 'acmeHQ',
        },
        {
          body: [{
            errorCode: 'INVALID_CROSS_REFERENCE_KEY',
            message: 'invalid cross reference id',
          }],
          httpHeaders: {},
          httpStatusCode: 400,
          referenceId: 'cfo',
        },
      ],
    });

    const result = await writeLinked(org(fake), [
      { refId: 'acmeHQ', sobject: 'Account', fields: { Name: 'Acme HQ' } },
      {
        refId: 'cfo',
        sobject: 'Contact',
        fields: { LastName: 'Vance', AccountId: { $ref: 'acmeHQ' } },
      },
    ]);

    assert.equal(result.ok, false);
    assert.deepEqual(result.ids, {},
      'after an allOrNone rollback nothing was created — no ids may survive');
    assert.equal(result.errors.acmeHQ?.[0]?.errorCode, 'PROCESSING_HALTED',
      'rolled-back siblings surface PROCESSING_HALTED');
    assert.equal(result.errors.cfo?.[0]?.errorCode, 'INVALID_CROSS_REFERENCE_KEY',
      'the genuinely failing subrequest keeps its own error');
  } finally {
    await fake.stop();
  }
});

test('top-level REST errors raise SalesforceApiError without leaking the token', async () => {
  const fake = await new FakeOrgServer().start();
  try {
    fake.reply(401, [{
      message: 'Session expired or invalid',
      errorCode: 'INVALID_SESSION_ID',
    }]);
    await assert.rejects(
      writeLinked(org(fake), [
        { refId: 'acmeHQ', sobject: 'Account', fields: { Name: 'Acme HQ' } },
      ]),
      (err: unknown) => {
        assert.ok(err instanceof SalesforceApiError);
        assert.equal(err.status, 401);
        assert.equal(err.errors[0]?.errorCode, 'INVALID_SESSION_ID');
        assert.ok(!err.message.includes(TOKEN),
          'the access token must never appear in error text');
        return true;
      });
  } finally {
    await fake.stop();
  }
});

// ---------------------------------------------------------------------------
// Protected fixtures stay wired to the code under test
// ---------------------------------------------------------------------------

test('protected docs fixtures pin the researched contract', () => {
  assert.equal(SOURCES.research.required, true);
  const urls: string[] = SOURCES.research.official_sources.map(
    (s: { url: string }) => s.url);
  assert.ok(urls.length >= 2, 'at least two official sources');
  assert.ok(urls.every((u) => u.startsWith('https://developer.salesforce.com/')),
    'provenance must point at first-party Salesforce docs');
  assert.equal(CONTRACT.api_version, 'v67.0');
  assert.equal(CONTRACT.composite.limits.max_subrequests, 25);
  assert.equal(CONTRACT.sobject_tree.limits.max_records_total, 200);
  assert.match(CONTRACT.composite.reference_syntax, /@\{referenceId\.FieldName\}/);
});
