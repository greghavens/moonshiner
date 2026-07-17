import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Policy } from './policy.ts';
import { ApprovalEngine } from './engine.ts';

const policy = () =>
  new Policy([
    { upTo: 100, approvers: ['manager'] },
    { upTo: 1000, approvers: ['manager', 'director'] },
    { approvers: ['manager', 'director', 'finance'] },
  ]);

const mk = () =>
  new ApprovalEngine(policy(), {
    manager: 'mara',
    director: 'devon',
    finance: 'fiona',
  });

test('every policy role must have an assigned approver', () => {
  assert.throws(
    () => new ApprovalEngine(policy(), { manager: 'mara', director: 'devon' }),
    /finance/,
  );
});

// ---------- submission ----------

test('a small expense routes to the manager only', () => {
  const e = mk();
  const snap = e.submit({ id: 'x1', submitter: 'sam', amount: 75, description: 'client lunch' });
  assert.equal(snap.status, 'pending');
  assert.deepEqual(snap.chain, ['manager']);
  assert.deepEqual(snap.pendingWith, { role: 'manager', approver: 'mara' });
});

test('threshold boundaries are inclusive', () => {
  const e = mk();
  assert.deepEqual(e.submit({ id: 'a', submitter: 'sam', amount: 100 }).chain, ['manager']);
  assert.deepEqual(e.submit({ id: 'b', submitter: 'sam', amount: 100.01 }).chain, [
    'manager',
    'director',
  ]);
});

test('duplicate expense ids are refused, even after settlement', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 50 });
  assert.throws(() => e.submit({ id: 'x1', submitter: 'pat', amount: 60 }), /x1/);
  e.approve('x1', 'mara');
  assert.throws(() => e.submit({ id: 'x1', submitter: 'pat', amount: 60 }), /x1/);
});

test('submissions are validated', () => {
  const e = mk();
  for (const bad of [0, -20, NaN, Infinity]) {
    assert.throws(() => e.submit({ id: `amt-${bad}`, submitter: 'sam', amount: bad }));
  }
  assert.throws(() => e.submit({ id: 'nosub', submitter: '', amount: 10 }));
});

test('get returns a copy and rejects unknown ids by name', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 50 });
  const snap = e.get('x1');
  snap.status = 'approved';
  snap.chain.push('intruder');
  assert.equal(e.get('x1').status, 'pending');
  assert.deepEqual(e.get('x1').chain, ['manager']);
  assert.throws(() => e.get('ghost'), /ghost/);
});

// ---------- the approval chain ----------

test('a mid-tier expense walks manager then director', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.equal(e.approve('x1', 'mara').status, 'pending');
  assert.deepEqual(e.get('x1').pendingWith, { role: 'director', approver: 'devon' });
  assert.equal(e.approve('x1', 'devon').status, 'approved');
  assert.equal(e.get('x1').pendingWith, null);
});

test('approvals must come in chain order', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.approve('x1', 'devon'), /mara/);
});

test('bystanders cannot approve', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.approve('x1', 'randy'), /mara/);
});

test('a top-tier expense needs all three approvals', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 5000 });
  e.approve('x1', 'mara');
  e.approve('x1', 'devon');
  assert.equal(e.get('x1').status, 'pending');
  assert.deepEqual(e.get('x1').pendingWith, { role: 'finance', approver: 'fiona' });
  assert.equal(e.approve('x1', 'fiona').status, 'approved');
});

test('settled expenses accept no further decisions', () => {
  const e = mk();
  e.submit({ id: 'ok', submitter: 'sam', amount: 50 });
  e.approve('ok', 'mara');
  assert.throws(() => e.approve('ok', 'mara'), /approved/);
  e.submit({ id: 'no', submitter: 'sam', amount: 50 });
  e.reject('no', 'mara', 'not a business cost');
  assert.throws(() => e.approve('no', 'mara'), /rejected/);
  assert.throws(() => e.reject('no', 'mara', 'again'), /rejected/);
});

test('nobody approves their own expense', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'mara', amount: 50 });
  assert.throws(() => e.approve('x1', 'mara'), /own/);
});

// ---------- rejection and resubmission ----------

test('rejection requires a non-empty reason', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.reject('x1', 'mara', ''), /reason/);
  assert.throws(() => e.reject('x1', 'mara', '   '), /reason/);
  assert.equal(e.reject('x1', 'mara', 'missing receipt').status, 'rejected');
});

test('only the current approver can reject', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.reject('x1', 'devon', 'jumping the queue'), /mara/);
});

test('only rejected expenses can be resubmitted', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.resubmit('x1'), /pending/);
  e.approve('x1', 'mara');
  e.approve('x1', 'devon');
  assert.throws(() => e.resubmit('x1'), /approved/);
});

test('resubmission restarts the chain from the top', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.approve('x1', 'mara');
  e.reject('x1', 'devon', 'wrong cost center');
  const snap = e.resubmit('x1');
  assert.equal(snap.status, 'pending');
  assert.deepEqual(snap.pendingWith, { role: 'manager', approver: 'mara' });
  // the earlier manager approval does not carry over
  assert.throws(() => e.approve('x1', 'devon'), /mara/);
  e.approve('x1', 'mara');
  e.approve('x1', 'devon');
  assert.equal(e.get('x1').status, 'approved');
});

test('resubmitting with a new amount recomputes the chain', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 5000, description: 'team offsite' });
  e.reject('x1', 'mara', 'way over budget');
  const snap = e.resubmit('x1', { amount: 90, description: 'team lunch instead' });
  assert.equal(snap.amount, 90);
  assert.equal(snap.description, 'team lunch instead');
  assert.deepEqual(snap.chain, ['manager']);
  assert.equal(e.approve('x1', 'mara').status, 'approved');
});

test('a resubmitted amount is validated like a new one', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.reject('x1', 'mara', 'no');
  assert.throws(() => e.resubmit('x1', { amount: -3 }));
  assert.equal(e.get('x1').status, 'rejected');
});

// ---------- delegation ----------

test('a delegate can approve on behalf of an out-of-office approver', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.setDelegate('mara', 'dana'); // set after submission, still counts
  e.approve('x1', 'dana');
  assert.deepEqual(e.get('x1').pendingWith, { role: 'director', approver: 'devon' });
});

test('delegation is one hop, never a chain', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.setDelegate('mara', 'dana');
  e.setDelegate('dana', 'eli');
  assert.throws(() => e.approve('x1', 'eli'), /mara/);
});

test('clearing a delegation revokes it', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.setDelegate('mara', 'dana');
  e.clearDelegate('mara');
  assert.throws(() => e.approve('x1', 'dana'), /mara/);
});

test('users cannot delegate to themselves', () => {
  const e = mk();
  assert.throws(() => e.setDelegate('mara', 'mara'));
});

test('delegation never lets a submitter touch their own expense', () => {
  const e = mk();
  // the submitter is the delegate
  e.submit({ id: 'x1', submitter: 'dana', amount: 500 });
  e.setDelegate('mara', 'dana');
  assert.throws(() => e.approve('x1', 'dana'), /own/);
  // the submitter is the assigned approver; their delegate cannot stand in
  e.submit({ id: 'x2', submitter: 'mara', amount: 500 });
  assert.throws(() => e.approve('x2', 'dana'), /own/);
});

test('rejection through a delegate works like approval', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.setDelegate('mara', 'dana');
  assert.equal(e.reject('x1', 'dana', 'duplicate claim').status, 'rejected');
});

// ---------- the audit trail ----------

test('a full lifecycle is recorded event by event', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  e.approve('x1', 'mara', 'looks fine');
  e.reject('x1', 'devon', 'wrong cost center');
  e.resubmit('x1', { amount: 80 });
  e.setDelegate('mara', 'dana');
  e.approve('x1', 'dana');

  assert.deepEqual(e.auditTrail('x1'), [
    { seq: 1, event: 'submitted', by: 'sam', amount: 500 },
    { seq: 2, event: 'approved', role: 'manager', by: 'mara', comment: 'looks fine' },
    { seq: 3, event: 'rejected', role: 'director', by: 'devon', reason: 'wrong cost center' },
    { seq: 4, event: 'resubmitted', by: 'sam', amount: 80 },
    { seq: 5, event: 'approved', role: 'manager', by: 'dana', onBehalfOf: 'mara' },
  ]);
});

test('failed attempts leave no trace in the audit trail', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 500 });
  assert.throws(() => e.approve('x1', 'devon'));
  assert.throws(() => e.reject('x1', 'mara', ''));
  assert.equal(e.auditTrail('x1').length, 1);
});

test('the audit trail is a copy, per expense, and unknown ids are named', () => {
  const e = mk();
  e.submit({ id: 'x1', submitter: 'sam', amount: 50 });
  e.submit({ id: 'x2', submitter: 'pat', amount: 60 });
  e.approve('x2', 'mara');
  const trail = e.auditTrail('x1');
  trail.push({ seq: 99, event: 'forged', by: 'evil', amount: 1 });
  trail[0].by = 'evil';
  assert.deepEqual(e.auditTrail('x1'), [{ seq: 1, event: 'submitted', by: 'sam', amount: 50 }]);
  assert.equal(e.auditTrail('x2').length, 2);
  assert.throws(() => e.auditTrail('ghost'), /ghost/);
});
