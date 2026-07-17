import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTicketRow, buildTicketRows, ProjectionError } from './ticket_view.ts';

function fullTicket(): Record<string, unknown> {
  return {
    id: 'T-1041',
    requester: {
      name: 'Mara Voss',
      contact: { email: 'Mara.Voss@Example.com', phone: '555-0141' },
    },
    org: { name: 'Halyard Freight', plan: 'pro' },
    thread: {
      messages: [
        { author: 'mara', body: 'Scanner keeps rebooting.' },
        { author: 'agent', body: 'Which firmware build is it on right now?' },
      ],
    },
    metrics: { sla: { breachAt: '2026-05-04T16:00:00Z' } },
  };
}

function expectProjectionError(fn: () => unknown, path: string): void {
  assert.throws(fn, (err: unknown) => {
    assert.ok(err instanceof ProjectionError, `expected ProjectionError, got ${String(err)}`);
    assert.equal(err.path, path);
    return true;
  });
}

test('a fully populated ticket projects every column', () => {
  assert.deepEqual(buildTicketRow(fullTicket()), {
    id: 'T-1041',
    requesterEmail: 'mara.voss@example.com',
    orgName: 'Halyard Freight',
    lastReply: 'Which firmware build is it on right now?',
    breachAt: '2026-05-04T16:00:00Z',
  });
});

test('long replies are trimmed to 80 characters', () => {
  const t = fullTicket();
  (t.thread as { messages: { author: string; body: string }[] }).messages.push({
    author: 'mara',
    body: 'y'.repeat(200),
  });
  assert.equal(buildTicketRow(t).lastReply, 'y'.repeat(80));
});

test('a ticket with no requester reports the requester path', () => {
  const t = fullTicket();
  delete t.requester;
  expectProjectionError(() => buildTicketRow(t), 'requester');
});

test('a null contact reports the contact path instead of crashing', () => {
  const t = fullTicket();
  (t.requester as Record<string, unknown>).contact = null;
  expectProjectionError(() => buildTicketRow(t), 'requester.contact');
});

test('a contact without an email reports the full email path', () => {
  const t = fullTicket();
  (t.requester as Record<string, unknown>).contact = { phone: '555-0141' };
  expectProjectionError(() => buildTicketRow(t), 'requester.contact.email');
});

test('an absent org falls back to (none)', () => {
  const t = fullTicket();
  delete t.org;
  assert.equal(buildTicketRow(t).orgName, '(none)');
});

test('a null org falls back to (none)', () => {
  const t = fullTicket();
  t.org = null;
  assert.equal(buildTicketRow(t).orgName, '(none)');
});

test('an org object without a name reports org.name', () => {
  const t = fullTicket();
  t.org = { plan: 'trial' };
  expectProjectionError(() => buildTicketRow(t), 'org.name');
});

test('a ticket with no thread yet has an empty lastReply', () => {
  const t = fullTicket();
  delete t.thread;
  assert.equal(buildTicketRow(t).lastReply, '');
});

test('an empty message list has an empty lastReply', () => {
  const t = fullTicket();
  t.thread = { messages: [] };
  assert.equal(buildTicketRow(t).lastReply, '');
});

test('an empty metrics object means no breach timestamp', () => {
  const t = fullTicket();
  t.metrics = {};
  assert.equal(buildTicketRow(t).breachAt, null);
});

test('missing metrics entirely means no breach timestamp', () => {
  const t = fullTicket();
  delete t.metrics;
  assert.equal(buildTicketRow(t).breachAt, null);
});

test('an explicit null breachAt stays null', () => {
  const t = fullTicket();
  t.metrics = { sla: { breachAt: null } };
  assert.equal(buildTicketRow(t).breachAt, null);
});

test('a batch keeps the good rows and reports one problem per bad ticket', () => {
  const second = fullTicket();
  (second.requester as Record<string, unknown>).contact = undefined;
  const fourth = fullTicket();
  fourth.id = 'T-1042';
  const { rows, problems } = buildTicketRows([fullTicket(), second, null, fourth, 'garbage']);
  assert.deepEqual(rows.map((r) => r.id), ['T-1041', 'T-1042']);
  assert.deepEqual(problems, [
    { index: 1, path: 'requester.contact' },
    { index: 2, path: '$' },
    { index: 4, path: '$' },
  ]);
});

test('projection errors carry the path in their message', () => {
  const t = fullTicket();
  delete t.requester;
  try {
    buildTicketRow(t);
    assert.fail('expected a throw');
  } catch (err) {
    assert.ok(err instanceof ProjectionError);
    assert.equal(err.name, 'ProjectionError');
    assert.match(err.message, /requester/);
  }
});
